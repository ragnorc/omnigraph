//! SPIKE: run every edition-2 source the repository holds through the
//! rewriter and both front ends; compare typed IR; time both pipelines.
//! Writes `target/spike-gq3/corpus.tsv` and prints a summary.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use super::migrate::{Options, Proof, compile_ir, migrate};
use super::tests::collect;
use crate::catalog::{Catalog, build_catalog};
use crate::query::ast::FileBody;
use crate::schema::parser::parse_schema;

struct Source {
    origin: String,
    text: String,
    catalog: Option<std::rc::Rc<Catalog>>,
}

fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// `--- schema`, `--- query` and `--- mutate` sections of a `.gqt` case.
fn gqt_sources(out: &mut Vec<Source>) {
    let mut files = Vec::new();
    collect(
        &root().join("crates/omnigraph-gqt/cases"),
        "gqt",
        &mut files,
    );
    collect(
        &root().join("crates/omnigraph-gqt/cases_slow"),
        "gqt",
        &mut files,
    );
    files.sort();
    for f in files {
        let text = std::fs::read_to_string(&f).unwrap();
        let mut sections: Vec<(String, String)> = Vec::new();
        for line in text.lines() {
            if let Some(header) = line.strip_prefix("--- ") {
                sections.push((header.to_string(), String::new()));
            } else if let Some((_, body)) = sections.last_mut() {
                body.push_str(line);
                body.push('\n');
            }
        }
        let catalog = sections
            .iter()
            .find(|(h, _)| h.trim() == "schema")
            .and_then(|(_, s)| parse_schema(s).ok())
            .and_then(|s| build_catalog(&s).ok())
            .map(std::rc::Rc::new);
        let name = f.strip_prefix(root()).unwrap_or(&f).display().to_string();
        for (i, (header, body)) in sections.iter().enumerate() {
            let kind = header.split_whitespace().next().unwrap_or("");
            if kind == "query" || kind == "mutate" {
                out.push(Source {
                    origin: format!("{name}#{i}"),
                    text: body.clone(),
                    catalog: catalog.clone(),
                });
            }
        }
    }
}

/// Fenced ```gq blocks in the user guide, developer docs and the skill.
fn doc_sources(out: &mut Vec<Source>) {
    let mut files = Vec::new();
    collect(&root().join("docs/user"), "md", &mut files);
    collect(&root().join("docs/dev"), "md", &mut files);
    collect(&root().join("skills"), "md", &mut files);
    files.sort();
    for f in files {
        let text = std::fs::read_to_string(&f).unwrap();
        let mut inside = false;
        let mut block = String::new();
        let mut n = 0;
        for line in text.lines() {
            if !inside && line.trim_start().starts_with("```gq") {
                inside = true;
                block.clear();
            } else if inside && line.trim_start().starts_with("```") {
                inside = false;
                n += 1;
                out.push(Source {
                    origin: format!("{}#{n}", f.strip_prefix(root()).unwrap_or(&f).display()),
                    text: block.clone(),
                    catalog: None,
                });
            } else if inside {
                block.push_str(line);
                block.push('\n');
            }
        }
    }
    let mut gq = Vec::new();
    collect(&root(), "gq", &mut gq);
    let fixture_schema =
        std::fs::read_to_string(root().join("crates/omnigraph/tests/fixtures/test.pg"))
            .ok()
            .and_then(|s| parse_schema(&s).ok())
            .and_then(|s| build_catalog(&s).ok())
            .map(std::rc::Rc::new);
    for f in gq {
        let rel = f.strip_prefix(root()).unwrap_or(&f).display().to_string();
        let catalog = rel
            .ends_with("fixtures/test.gq")
            .then(|| fixture_schema.clone())
            .flatten();
        out.push(Source {
            origin: rel,
            text: std::fs::read_to_string(&f).unwrap(),
            catalog,
        });
    }
}

#[test]
fn whole_corpus_through_the_rewriter_and_both_front_ends() {
    let mut sources = Vec::new();
    gqt_sources(&mut sources);
    doc_sources(&mut sources);
    let mut tally: BTreeMap<String, usize> = BTreeMap::new();
    let mut rows = vec!["origin\toutcome\tdetail".to_string()];
    let mut bump = |k: &str| *tally.entry(k.to_string()).or_default() += 1;
    let (mut t2, mut t3, mut timed) = (Duration::ZERO, Duration::ZERO, 0usize);
    let mut notes_count = 0;
    let mut swap_differs = 0;
    for s in &sources {
        let Ok(original) = crate::query::parser::parse_query_diagnostic_edition2(&s.text) else {
            bump("edition-2 parse refused (expect-error case)");
            rows.push(format!("{}\tv2-parse-refused\t", s.origin));
            continue;
        };
        let is_decl = matches!(original.body, FileBody::Queries(ref d) if !d.is_empty())
            || matches!(original.body, FileBody::Explain(_));
        let m = match migrate(&s.text, s.catalog.as_deref(), Options::default()) {
            Ok(m) => m,
            Err(e) => {
                bump("rewriter error");
                rows.push(format!("{}\trewriter-error\t{}", s.origin, e.0));
                continue;
            }
        };
        notes_count += m.notes.len();
        for n in &m.notes {
            rows.push(format!("{}\tnote\t{n}", s.origin));
        }
        // Idempotence.
        let again = migrate(&m.text, s.catalog.as_deref(), Options::default()).unwrap();
        assert_eq!(again.text, m.text, "{}", s.origin);
        // The rewritten text must parse under edition 3.
        let parsed3 = crate::query::parser::parse_query_diagnostic(&m.text);
        if let Err(d) = &parsed3 {
            bump("edition-3 parse refused the rewrite");
            rows.push(format!(
                "{}\tv3-parse-refused\t{} {} fix={:?} | {}",
                s.origin,
                d.code,
                d.message,
                d.fix,
                m.text.replace('\n', " ")
            ));
            continue;
        }
        if !is_decl {
            bump("statement (unchanged)");
            continue;
        }
        let Some(catalog) = s.catalog.as_deref() else {
            bump("declaration, parse-only (no schema)");
            continue;
        };
        for (name, proof) in &m.proof {
            match proof {
                Proof::Equal => bump("declaration, IR equal"),
                Proof::OriginalRefused(e2) => {
                    bump("declaration, edition 2 refuses (expect-error case)");
                    let c2 = code_of(e2);
                    let c3 = match crate::query::parser::parse_query_diagnostic(&m.text) {
                        Err(d) => d.code.as_str().to_string(),
                        Ok(f) => match &f.body {
                            FileBody::Queries(ds) => ds
                                .iter()
                                .find(|d| &d.name == name)
                                .map(|d| match compile_ir(d, catalog) {
                                    Ok(_) => "ok".to_string(),
                                    Err(e) => code_of(&e),
                                })
                                .unwrap_or_default(),
                            _ => String::new(),
                        },
                    };
                    if c2 == c3 {
                        bump("  expect-error: same code in edition 3");
                    } else {
                        bump(&format!("  expect-error: code changes {c2} -> {c3}"));
                        rows.push(format!(
                            "{}	code-change	{name}: {c2} -> {c3} | {e2}",
                            s.origin
                        ));
                    }
                }
                Proof::Differs(d) => {
                    bump("declaration, IR differs");
                    rows.push(format!("{}\tir-differs\t{name}: {d}", s.origin));
                }
                Proof::RewriteRefused(d) => {
                    let code = d.split(':').next().unwrap_or("").to_string();
                    bump(&format!(
                        "declaration, edition 3 refuses the rewrite ({})",
                        if d.contains("T68") {
                            "T68 incoming triple"
                        } else {
                            &code
                        }
                    ));
                    rows.push(format!("{}\tv3-refused\t{name}: {d}", s.origin));
                    if d.contains("T68") {
                        let swapped = migrate(
                            &s.text,
                            Some(catalog),
                            Options {
                                swap_incoming: true,
                            },
                        )
                        .unwrap();
                        for (n2, p2) in &swapped.proof {
                            if n2 == name {
                                rows.push(format!("{}\tswap\t{name}: {p2:?}", s.origin));
                                if matches!(p2, Proof::Differs(_)) {
                                    swap_differs += 1;
                                }
                            }
                        }
                    }
                }
                Proof::NotChecked => bump("not checked"),
            }
        }
        // Cost: parse + type-check + lower, both editions, 20 rounds.
        if let (Ok(FileBody::Queries(d2)), Ok(f3)) = (Ok::<_, ()>(original.body.clone()), &parsed3)
        {
            let FileBody::Queries(d3) = &f3.body else {
                continue;
            };
            if d2.iter().all(|d| compile_ir(d, catalog).is_ok())
                && d3.iter().all(|d| compile_ir(d, catalog).is_ok())
            {
                let a = Instant::now();
                for _ in 0..20 {
                    let f = crate::query::parser::parse_query_diagnostic_edition2(&s.text).unwrap();
                    if let FileBody::Queries(d) = f.body {
                        for d in &d {
                            compile_ir(d, catalog).unwrap();
                        }
                    }
                }
                t2 += a.elapsed();
                let b = Instant::now();
                for _ in 0..20 {
                    let f = crate::query::parser::parse_query_diagnostic(&m.text).unwrap();
                    if let FileBody::Queries(d) = f.body {
                        for d in &d {
                            compile_ir(d, catalog).unwrap();
                        }
                    }
                }
                t3 += b.elapsed();
                timed += 1;
            }
        }
    }
    let dir = root().join("target/spike-gq3");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("corpus.tsv"), rows.join("\n")).unwrap();
    let mut summary = format!("sources: {}\n", sources.len());
    for (k, v) in &tally {
        summary.push_str(&format!("  {v:5}  {k}\n"));
    }
    summary.push_str(&format!("  {notes_count:5}  result-column renames noted\n"));
    summary.push_str(&format!(
        "  {swap_differs:5}  T68 refusals whose swapped rewrite compiles to different IR\n"
    ));
    summary.push_str(&format!(
        "cost over {timed} sources x 20 rounds (parse + check + lower): edition 2 {:?}, edition 3 {:?} (ratio {:.2})\n",
        t2,
        t3,
        t3.as_secs_f64() / t2.as_secs_f64().max(1e-9)
    ));
    std::fs::write(dir.join("corpus-summary.txt"), &summary).unwrap();
    eprintln!("{summary}");
}

fn code_of(message: &str) -> String {
    // `type error: T33: …`, `parse error: …`, `plan error: P002: …`
    let rest = message.split_once("error: ").map_or(message, |(_, r)| r);
    let head = rest.split(':').next().unwrap_or("");
    if head.len() <= 4
        && head
            .chars()
            .next()
            .is_some_and(|c| matches!(c, 'T' | 'Q' | 'P'))
        && head[1..].chars().all(|c| c.is_ascii_digit())
    {
        head.to_string()
    } else if message.starts_with("parse error") {
        "Q001".to_string()
    } else {
        format!("other({})", rest.chars().take(40).collect::<String>())
    }
}

/// Every `--- schema` of the GQT corpus that today's `.pg` grammar accepts
/// parses under the one grammar's `file` rule.
#[test]
fn every_gqt_schema_parses_under_the_one_grammar() {
    use pest::Parser;
    let mut files = Vec::new();
    collect(
        &root().join("crates/omnigraph-gqt/cases"),
        "gqt",
        &mut files,
    );
    collect(
        &root().join("crates/omnigraph-gqt/cases_slow"),
        "gqt",
        &mut files,
    );
    let (mut accepted, mut refused) = (0, Vec::new());
    for f in files {
        let text = std::fs::read_to_string(&f).unwrap();
        let mut schema = None;
        let mut current: Option<String> = None;
        for line in text.lines() {
            if let Some(h) = line.strip_prefix("--- ") {
                if current.is_some() && schema.is_none() {
                    schema = current.take();
                }
                current = (h.trim() == "schema").then(String::new);
            } else if let Some(c) = current.as_mut() {
                c.push_str(line);
                c.push('\n');
            }
        }
        let Some(schema) = schema.or(current) else {
            continue;
        };
        if parse_schema(&schema).is_err() {
            continue;
        }
        accepted += 1;
        if let Err(e) = super::Gq3Parser::parse(super::Rule::file, &schema) {
            refused.push(format!("{}: {e}", f.display()));
        }
    }
    eprintln!(
        "GQT schemas: {accepted} accepted by schema.pest; {} refused by the one grammar",
        refused.len()
    );
    for r in refused.iter().take(5) {
        eprintln!("{r}");
    }
    assert!(refused.is_empty());
}
