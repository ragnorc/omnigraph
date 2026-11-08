//! SPIKE: every edition-2 text the repository stores, through the rewriter.
//! Writes `target/spike-gq3-rewriter/` (`corpus.tsv`, `summary.md`,
//! `rewrites.md`) and prints the summary; fails when a proof fails.
//!
//! Sources: the query text of every `--- query`, `--- mutate` and
//! `--- concurrent` step of `crates/omnigraph-gqt/cases/**/*.gqt` and of
//! `benchmarks/**/*.gqt` (the case's `--- schema` is the catalog); every
//! fenced ```gq block and `.gq` file under `docs/` and `skills/omnigraph`
//! (a ```pg block of the same page is the catalog, when one types it).

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use super::{Migration, Row, apply_rows_in_edition_2, migrate, typed_ir};
use crate::catalog::{Catalog, build_catalog};
use crate::query::ast::FileBody;
use crate::query::parser::parse_query_diagnostic;
use crate::schema::parser::parse_schema;

pub(crate) struct Source {
    pub group: &'static str,
    pub origin: String,
    pub text: String,
    pub catalogs: Vec<Rc<Catalog>>,
    /// The schema text of each catalog, index for index.
    pub schemas: Vec<String>,
}

pub(crate) fn root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn collect(dir: &Path, ext: &str, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            // This spike's own artifacts quote every rewritten text.
            if path
                .file_name()
                .is_some_and(|n| n == "target" || n == "node_modules" || n == "gq3-rewriter")
            {
                continue;
            }
            collect(&path, ext, out);
        } else if path.extension().is_some_and(|e| e == ext) {
            out.push(path);
        }
    }
}

fn rel(path: &Path) -> String {
    path.strip_prefix(root())
        .unwrap_or(path)
        .display()
        .to_string()
}

fn catalog_of(schema: &str) -> Option<Rc<Catalog>> {
    parse_schema(schema)
        .ok()
        .and_then(|s| build_catalog(&s).ok())
        .map(Rc::new)
}

/// The step texts of a `.gqt` file: `--- query` and `--- mutate` bodies,
/// and each session line of a `--- concurrent` block.
pub(crate) fn gqt_sources(group: &'static str, dir: &Path, out: &mut Vec<Source>) {
    let mut files = Vec::new();
    collect(dir, "gqt", &mut files);
    files.sort();
    for f in files {
        let text = std::fs::read_to_string(&f).unwrap();
        let mut sections: Vec<(String, String)> = Vec::new();
        for line in text.split_inclusive('\n') {
            if let Some(header) = line.strip_prefix("--- ") {
                sections.push((header.trim_end().to_string(), String::new()));
            } else if let Some((_, body)) = sections.last_mut() {
                body.push_str(line);
            }
        }
        let schema_text = sections
            .iter()
            .find(|(h, _)| h.trim() == "schema")
            .map(|(_, s)| s.clone());
        let catalogs: Vec<Rc<Catalog>> = schema_text
            .as_deref()
            .and_then(catalog_of)
            .into_iter()
            .collect();
        let schemas: Vec<String> = if catalogs.is_empty() {
            Vec::new()
        } else {
            schema_text.into_iter().collect()
        };
        let name = rel(&f);
        for (i, (header, body)) in sections.iter().enumerate() {
            let kind = header.split_whitespace().next().unwrap_or("");
            match kind {
                "query" | "mutate" => out.push(Source {
                    group,
                    origin: format!("{name}#{i}:{kind}"),
                    text: body.trim_end_matches('\n').to_string() + "\n",
                    catalogs: catalogs.clone(),
                    schemas: schemas.clone(),
                }),
                "concurrent" => {
                    for (j, line) in body.lines().enumerate() {
                        if let Some((label, query)) = line.split_once(": ")
                            && label != "order"
                            && query.trim_start().starts_with("query")
                        {
                            out.push(Source {
                                group,
                                origin: format!("{name}#{i}.{j}:concurrent"),
                                text: query.to_string(),
                                catalogs: catalogs.clone(),
                                schemas: schemas.clone(),
                            });
                        }
                    }
                }
                _ => {}
            }
        }
    }
}

/// Fenced ```gq blocks of the markdown files under `dir`, and its `.gq`
/// files.
pub(crate) fn doc_sources(group: &'static str, dir: &Path, out: &mut Vec<Source>) {
    let mut files = Vec::new();
    collect(dir, "md", &mut files);
    files.sort();
    for f in files {
        let text = std::fs::read_to_string(&f).unwrap();
        let mut blocks: Vec<(String, String)> = Vec::new();
        let mut current: Option<(String, String)> = None;
        for line in text.split_inclusive('\n') {
            let trimmed = line.trim_start();
            match &mut current {
                None => {
                    if let Some(info) = trimmed.strip_prefix("```") {
                        let lang = info.split_whitespace().next().unwrap_or("").to_string();
                        current = Some((lang, String::new()));
                    }
                }
                Some((lang, body)) => {
                    if trimmed.starts_with("```") {
                        blocks.push((std::mem::take(lang), std::mem::take(body)));
                        current = None;
                    } else {
                        body.push_str(line);
                    }
                }
            }
        }
        let schemas: Vec<&str> = blocks
            .iter()
            .filter(|(l, _)| l == "pg")
            .map(|(_, b)| b.as_str())
            .collect();
        let mut catalogs: Vec<Rc<Catalog>> = Vec::new();
        let mut schema_texts: Vec<String> = Vec::new();
        for text in &schemas {
            if let Some(c) = catalog_of(text) {
                catalogs.push(c);
                schema_texts.push(text.to_string());
            }
        }
        if schemas.len() > 1
            && let Some(all) = catalog_of(&schemas.join("\n"))
        {
            catalogs.push(all);
            schema_texts.push(schemas.join("\n"));
        }
        let name = rel(&f);
        for (n, (lang, body)) in blocks.iter().enumerate() {
            if lang == "gq" {
                out.push(Source {
                    group,
                    origin: format!("{name}#{n}"),
                    text: body.clone(),
                    catalogs: catalogs.clone(),
                    schemas: schema_texts.clone(),
                });
            }
        }
    }
    let mut gq = Vec::new();
    collect(dir, "gq", &mut gq);
    gq.sort();
    for f in gq {
        out.push(Source {
            group,
            origin: rel(&f),
            text: std::fs::read_to_string(&f).unwrap(),
            catalogs: Vec::new(),
            schemas: Vec::new(),
        });
    }
}

pub(crate) fn all_sources() -> Vec<Source> {
    let mut sources = Vec::new();
    gqt_sources(
        "gqt cases",
        &root().join("crates/omnigraph-gqt/cases"),
        &mut sources,
    );
    gqt_sources("benchmarks", &root().join("benchmarks"), &mut sources);
    doc_sources("docs", &root().join("docs"), &mut sources);
    doc_sources("skill", &root().join("skills/omnigraph"), &mut sources);
    sources
}

/// The catalog the declarations of `text` type-check against, the first
/// that admits every declaration; else the first one; else none.
fn pick_catalog(text: &str, catalogs: &[Rc<Catalog>]) -> Option<usize> {
    if catalogs.len() <= 1 {
        return (!catalogs.is_empty()).then_some(0);
    }
    let file = parse_query_diagnostic(text).ok()?;
    let FileBody::Queries(decls) = &file.body else {
        return Some(0);
    };
    catalogs
        .iter()
        .position(|c| {
            decls
                .iter()
                .all(|d| crate::query::typecheck::typecheck_query_decl(c, d).is_ok())
        })
        .or(Some(0))
}

#[derive(Default)]
struct Tally {
    files: BTreeSet<String>,
    sources: usize,
    edition_3_already: usize,
    v2_parse_refused: usize,
    statements: usize,
    declarations: usize,
    reads: usize,
    writes: usize,
    no_catalog: usize,
    touched: usize,
    untouched: usize,
    with_refusal: usize,
    rows: BTreeMap<Row, usize>,
    /// Declarations that use a row's spelling, rewritten or refused.
    uses: BTreeMap<Row, usize>,
    refusals: BTreeMap<String, usize>,
    notes: BTreeMap<&'static str, usize>,
    proof_failures: usize,
    /// Declarations whose list `contains` was rewritten to `in` and then
    /// compiled by today's compiler: typed IR equal, differing, or refused.
    in_ir_equal: usize,
    /// Declarations today's type checker refuses against the case schema
    /// (expect-error steps), and those of them the rewriter edits.
    v2_refused: usize,
    v2_refused_rewritten: BTreeMap<Row, usize>,
    in_ir_differs: Vec<String>,
}

fn refusal_class(reason: &str) -> String {
    let classes = [
        ("a body of", "write: several statements in one body"),
        (
            "T54 in edition 2",
            "ranking: `asc` on a rank key (edition-2 T54) would become legal",
        ),
        (
            "needs the schema",
            "contains: list or String undecidable without a schema",
        ),
        (
            "duplicate arms",
            "rrf: duplicate arms (edition-3 type error)",
        ),
        (
            "arms over different bindings",
            "rrf: arms over different bindings",
        ),
        (
            "second ranking",
            "ranking: a second ranking beside the window",
        ),
        (
            "not the leading `order` key",
            "ranking: not the leading order key",
        ),
        (
            "no window to size",
            "ranking: nearest/rrf in return with no ranking order key",
        ),
        ("no `limit` to size", "nearest: no limit to size k"),
        ("would collide", "ranking: $distance/$score already a name"),
        ("comment between", "write: comment inside the moved range"),
        ("inside `match`", "ranking: a ranking call inside match"),
        ("field is not", "ranking: field not $var.property"),
    ];
    classes
        .iter()
        .find(|(needle, _)| reason.contains(needle))
        .map(|(_, class)| class.to_string())
        .unwrap_or_else(|| reason.to_string())
}

#[test]
fn whole_corpus_through_the_rewriter() {
    let sources = all_sources();
    let out_dir = root().join("target/spike-gq3-rewriter");
    std::fs::create_dir_all(&out_dir).unwrap();
    let mut tallies: BTreeMap<&'static str, Tally> = BTreeMap::new();
    let mut tsv = vec!["group\torigin\toutcome\tdetail".to_string()];
    let mut rewrites = String::new();
    let mut export: Vec<String> = Vec::new();
    let mut failures = Vec::new();
    for s in &sources {
        let t = tallies.entry(s.group).or_default();
        t.files
            .insert(s.origin.split('#').next().unwrap_or("").to_string());
        t.sources += 1;
        if super::is_edition_3(&s.text) {
            t.edition_3_already += 1;
            tsv.push(format!("{}\t{}\tedition-3\t", s.group, s.origin));
            continue;
        }
        let Ok(file) = parse_query_diagnostic(&s.text) else {
            t.v2_parse_refused += 1;
            tsv.push(format!("{}\t{}\tv2-parse-refused\t", s.group, s.origin));
            continue;
        };
        let decl_count = match &file.body {
            FileBody::Queries(d) => d.len(),
            FileBody::Explain(_) => 1,
            _ => 0,
        };
        if decl_count == 0 {
            t.statements += 1;
        }
        let picked = pick_catalog(&s.text, &s.catalogs);
        let catalog = picked.map(|i| s.catalogs[i].as_ref());
        let schema = picked.and_then(|i| s.schemas.get(i).cloned());
        let m: Migration = migrate(&s.text, catalog).expect("parsed above");
        // Idempotence: the output already carries the edition line, so a
        // second run is a no-op, and the residue check shows nothing is
        // left for one to rewrite.
        let again = migrate(&m.text, catalog).unwrap();
        assert_eq!(again.text, m.text, "{}", s.origin);
        let proof_ok = m.proof.non_overlapping
            && m.proof.each_edit_rendered_once
            && m.proof.confined
            && (m.proof.no_residue || !m.refusals.is_empty());
        if !proof_ok {
            t.proof_failures += 1;
            failures.push(format!("{}: {:?}", s.origin, m.proof.failures));
        }
        let parsed_decls = match &file.body {
            FileBody::Queries(d) => d.clone(),
            FileBody::Explain(d) => vec![d.clone()],
            _ => Vec::new(),
        };
        for d in &m.decls {
            t.declarations += 1;
            if let (Some(catalog), Some(ast)) =
                (catalog, parsed_decls.iter().find(|x| x.name == d.name))
                && crate::query::typecheck::typecheck_query_decl(catalog, ast).is_err()
            {
                t.v2_refused += 1;
                for row in d.rows.keys() {
                    *t.v2_refused_rewritten.entry(*row).or_default() += 1;
                }
                tsv.push(format!("{}\t{}\tv2-refused\t{}", s.group, s.origin, d.name));
            }
            if d.is_write {
                t.writes += 1;
            } else {
                t.reads += 1;
            }
            if catalog.is_none() {
                t.no_catalog += 1;
            }
            let refused: Vec<_> = m.refusals.iter().filter(|r| r.decl == d.name).collect();
            let mut used: BTreeSet<Row> = d.rows.keys().copied().collect();
            used.extend(refused.iter().filter_map(|r| r.row));
            for row in &used {
                *t.uses.entry(*row).or_default() += 1;
            }
            if !refused.is_empty() {
                t.with_refusal += 1;
            } else if d.rows.is_empty() {
                t.untouched += 1;
            } else {
                t.touched += 1;
            }
            for (row, n) in &d.rows {
                *t.rows.entry(*row).or_default() += n;
            }
        }
        for r in &m.refusals {
            *t.refusals.entry(refusal_class(&r.reason)).or_default() += 1;
            tsv.push(format!(
                "{}\t{}\trefused\t{}: {}",
                s.group,
                s.origin,
                r.decl,
                r.reason.replace(['\n', '\t'], " ")
            ));
        }
        for n in &m.notes {
            *t.notes.entry(n.kind).or_default() += 1;
            tsv.push(format!(
                "{}\t{}\tnote:{}\t{}: {}",
                s.group,
                s.origin,
                n.kind,
                n.decl,
                n.detail.replace(['\n', '\t'], " ")
            ));
        }
        if let Some(catalog) = catalog
            && m.decls
                .iter()
                .any(|d| d.rows.contains_key(&Row::ListContains))
        {
            let rewritten =
                apply_rows_in_edition_2(&s.text, Some(catalog), &[Row::ListContains]).unwrap();
            let decls_of = |text: &str| match parse_query_diagnostic(text).map(|f| f.body) {
                Ok(FileBody::Queries(d)) => d,
                Ok(FileBody::Explain(d)) => vec![d],
                _ => Vec::new(),
            };
            let (old, new) = (decls_of(&s.text), decls_of(&rewritten));
            for d in m
                .decls
                .iter()
                .filter(|d| d.rows.contains_key(&Row::ListContains))
            {
                let a = old
                    .iter()
                    .find(|x| x.name == d.name)
                    .map(|x| typed_ir(x, catalog));
                let b = new
                    .iter()
                    .find(|x| x.name == d.name)
                    .map(|x| typed_ir(x, catalog));
                match (a, b) {
                    (Some(a), Some(b)) if a == b => t.in_ir_equal += 1,
                    (a, b) => t
                        .in_ir_differs
                        .push(format!("{}:{}: {a:?} / {b:?}", s.origin, d.name)),
                }
            }
        }
        let rows: Vec<String> = m
            .decls
            .iter()
            .flat_map(|d| d.rows.iter().map(|(r, n)| format!("{r:?}x{n}")))
            .collect();
        tsv.push(format!(
            "{}\t{}\t{}\t{}",
            s.group,
            s.origin,
            if m.refusals.is_empty() {
                "rewritten"
            } else {
                "partial"
            },
            rows.join(",")
        ));
        export.push(
            serde_json::json!({
                "group": s.group,
                "origin": s.origin,
                "schema": schema,
                "before": s.text,
                "after": m.text,
                "rows": m.decls.iter().flat_map(|d| d.rows.keys().map(|r| format!("{r:?}"))).collect::<BTreeSet<_>>(),
                "refused": !m.refusals.is_empty(),
            })
            .to_string(),
        );
        if !m.edits.is_empty() {
            let _ = writeln!(
                rewrites,
                "### {}\n\n```gq\n{}```\n\n```gq\n{}```\n",
                s.origin,
                ensure_newline(&s.text),
                ensure_newline(&m.text)
            );
        }
    }
    for t in tallies.values() {
        failures.extend(
            t.in_ir_differs
                .iter()
                .map(|d| format!("`in` IR differs: {d}")),
        );
    }
    std::fs::write(out_dir.join("corpus.tsv"), tsv.join("\n") + "\n").unwrap();
    std::fs::write(out_dir.join("rewrites.md"), rewrites).unwrap();
    std::fs::write(out_dir.join("corpus.jsonl"), export.join("\n") + "\n").unwrap();
    let summary = summarize(&tallies);
    std::fs::write(out_dir.join("summary.md"), &summary).unwrap();
    println!("{summary}");
    assert!(
        failures.is_empty(),
        "proof failures:\n{}",
        failures.join("\n")
    );
}

fn ensure_newline(s: &str) -> String {
    if s.ends_with('\n') {
        s.to_string()
    } else {
        format!("{s}\n")
    }
}

fn summarize(tallies: &BTreeMap<&'static str, Tally>) -> String {
    let groups: Vec<&&str> = tallies.keys().collect();
    let mut out = String::new();
    let header = |out: &mut String| {
        let _ = write!(out, "| |");
        for g in &groups {
            let _ = write!(out, " {g} |");
        }
        let _ = writeln!(out, " total |");
        let _ = write!(out, "|---|");
        for _ in &groups {
            let _ = write!(out, "---:|");
        }
        let _ = writeln!(out, "---:|");
    };
    let row = |out: &mut String, label: &str, f: &dyn Fn(&Tally) -> usize| {
        let _ = write!(out, "| {label} |");
        let mut total = 0;
        for g in &groups {
            let v = f(&tallies[**g]);
            total += v;
            let _ = write!(out, " {v} |");
        }
        let _ = writeln!(out, " {total} |");
    };
    header(&mut out);
    row(&mut out, "files", &|t| t.files.len());
    row(&mut out, "query texts", &|t| t.sources);
    row(&mut out, "already `gq 3;`", &|t| t.edition_3_already);
    row(&mut out, "not edition-2 text (parse refused)", &|t| {
        t.v2_parse_refused
    });
    row(&mut out, "statements (no declaration)", &|t| t.statements);
    row(&mut out, "declarations", &|t| t.declarations);
    row(&mut out, "  reads", &|t| t.reads);
    row(&mut out, "  writes", &|t| t.writes);
    row(&mut out, "  without a schema", &|t| t.no_catalog);
    row(
        &mut out,
        "  refused by today's type checker (expect-error steps)",
        &|t| t.v2_refused,
    );
    row(&mut out, "declarations rewritten", &|t| t.touched);
    row(&mut out, "declarations untouched", &|t| t.untouched);
    row(&mut out, "declarations with a refusal", &|t| t.with_refusal);
    row(&mut out, "proof failures", &|t| t.proof_failures);
    row(
        &mut out,
        "list `contains` -> `in`: typed IR equal (today's compiler)",
        &|t| t.in_ir_equal,
    );
    row(
        &mut out,
        "list `contains` -> `in`: typed IR differs",
        &|t| t.in_ir_differs.len(),
    );
    let _ = writeln!(out, "\n**Edits per row** (constructs rewritten)\n");
    header(&mut out);
    for r in Row::ALL {
        row(&mut out, r.name(), &|t| {
            t.rows.get(&r).copied().unwrap_or(0)
        });
    }
    let _ = writeln!(
        out,
        "\n**Declarations using each spelling** (rewritten or refused)\n"
    );
    header(&mut out);
    for r in Row::ALL {
        row(&mut out, r.name(), &|t| {
            t.uses.get(&r).copied().unwrap_or(0)
        });
    }
    let _ = writeln!(
        out,
        "\n**Of those, in declarations today's type checker refuses** (expect-error steps whose text the rewriter edits)\n"
    );
    header(&mut out);
    for r in Row::ALL {
        row(&mut out, r.name(), &|t| {
            t.v2_refused_rewritten.get(&r).copied().unwrap_or(0)
        });
    }
    let _ = writeln!(out, "\n**Refusals**\n");
    let classes: BTreeSet<&String> = tallies.values().flat_map(|t| t.refusals.keys()).collect();
    header(&mut out);
    for c in classes {
        row(&mut out, c, &|t| t.refusals.get(c).copied().unwrap_or(0));
    }
    let _ = writeln!(out, "\n**Notes** (observations a rewrite carries)\n");
    let kinds: BTreeSet<&&str> = tallies.values().flat_map(|t| t.notes.keys()).collect();
    header(&mut out);
    for k in kinds {
        row(&mut out, k, &|t| t.notes.get(*k).copied().unwrap_or(0));
    }
    out
}
