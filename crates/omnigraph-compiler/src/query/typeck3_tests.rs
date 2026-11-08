//! SPIKE: the edition-3 type checker's tests (RFC 2026-11-07-gq-3, section
//! 10, "Type-check tests"), the before-and-after asset type-checked against
//! its schema, and the edition-2 corpus checked by both editions.

use std::collections::BTreeMap;

use super::parser3::parse_query3;
use super::typeck3::{Checked3, compile3};
use crate::catalog::{Catalog, build_catalog};
use crate::ir::core3::*;
use crate::schema::parser::parse_schema;

/// The asset's shared schema block (`pg`).
pub(crate) fn asset_schema_text() -> String {
    let text = asset_text();
    let start = text.find("```pg\n").expect("schema block") + 6;
    let end = start + text[start..].find("```").unwrap();
    text[start..end].to_string()
}

pub(crate) fn asset_text() -> String {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("src/query/testdata/2026-11-07-gq-3-before-and-after.md");
    std::fs::read_to_string(path).expect("asset copy")
}

/// The asset's schema as today's `.pg` grammar reads it. The asset writes
/// `@index(fulltext)`, which today's schema parser refuses ("@index on Doc
/// references unknown property 'fulltext'"): a String property's `@index` is
/// already its full-text index. Finding 1 of the report.
pub(crate) fn asset_schema_pg() -> String {
    asset_schema_text().replace("@index(fulltext)", "@index")
}

pub(crate) fn asset_catalog() -> Catalog {
    build_catalog(&parse_schema(&asset_schema_pg()).expect("asset schema parses")).unwrap()
}

#[test]
fn the_asset_schema_is_not_todays_pg() {
    let e = parse_schema(&asset_schema_text()).unwrap_err().to_string();
    assert!(e.contains("unknown property 'fulltext'"), "{e}");
}

/// Check `src` (without the edition line) against the asset's schema; one
/// result per declaration.
#[track_caller]
pub(crate) fn check(src: &str) -> Vec<(String, Result<Checked3, super::diagnostic::QueryDiagnostic>)> {
    compile3(&asset_catalog(), &format!("gq 3;\n{src}")).unwrap_or_else(|e| panic!("parse: {e}\n{src}"))
}

/// The code of the first refused declaration, or `ok`.
#[track_caller]
pub(crate) fn code(src: &str) -> String {
    for (_, r) in check(src) {
        if let Err(e) = r {
            return e.code.as_str().to_string();
        }
    }
    "ok".into()
}

#[track_caller]
pub(crate) fn ok(src: &str) -> CoreDecl {
    let mut out = None;
    for (n, r) in check(src) {
        match r {
            Ok(c) => out = Some(c.core),
            Err(e) => panic!("`{n}` refused: {e}\n{src}"),
        }
    }
    out.unwrap()
}

#[track_caller]
pub(crate) fn refused(src: &str, want: &str) -> String {
    for (_, r) in check(src) {
        if let Err(e) = r {
            assert_eq!(e.code.as_str(), want, "{e}\n{src}");
            return format!("{e}");
        }
    }
    panic!("accepted, expected {want}:\n{src}");
}

pub(crate) fn read_of(c: &CoreDecl) -> &CoreRead {
    match c {
        CoreDecl::Read(r) => r,
        CoreDecl::Write(_) => panic!("a write"),
    }
}

fn asset_blocks() -> Vec<(Option<String>, String)> {
    let mut out = Vec::new();
    let mut inside = false;
    let mut block = String::new();
    for line in asset_text().lines() {
        if !inside && line.trim_start().starts_with("```gq") {
            inside = true;
            block.clear();
        } else if inside && line.trim_start().starts_with("```") {
            inside = false;
            let mark = block
                .lines()
                .next()
                .and_then(|l| l.trim().strip_prefix("// "))
                .filter(|l| l.starts_with("edition"))
                .map(str::to_string);
            out.push((mark, block.clone()));
        } else if inside {
            block.push_str(line);
            block.push('\n');
        }
    }
    out
}

/// Every edition-3 example of the before-and-after asset (the blocks marked
/// `// edition 3` and the unchanged ones), type-checked against the asset's
/// schema. The pinned map is the outcome per declaration; every refusal in
/// it is a finding in docs/spikes/gq3-typeck.md.
#[test]
fn every_edition3_example_of_the_asset_type_checks() {
    let mut outcome: BTreeMap<String, String> = BTreeMap::new();
    for (mark, block) in asset_blocks() {
        if mark.as_deref() == Some("edition 2") {
            continue;
        }
        let later = mark.as_deref().is_some_and(|m| m.contains("later"));
        // One declaration at a time for the later-stage block, whose forms
        // are refused at different layers.
        let parts: Vec<String> = if later {
            block
                .split("\nquery ")
                .filter(|p| p.contains('{'))
                .map(|p| {
                    if p.trim_start().starts_with("query ") || p.starts_with("//") {
                        p.lines().filter(|l| !l.starts_with("//")).collect::<Vec<_>>().join("\n")
                    } else {
                        format!("query {p}")
                    }
                })
                .collect()
        } else {
            vec![block.clone()]
        };
        for part in parts {
            let name = part
                .lines()
                .find_map(|l| l.trim_start().strip_prefix("query "))
                .and_then(|l| l.split('(').next())
                .unwrap_or("?")
                .to_string();
            let key = |n: String| if later { format!("{n} (later stage)") } else { n };
            let file = match parse_query3(&format!("gq 3;\n{part}")) {
                Ok(f) => f,
                Err(e) => {
                    outcome.insert(key(name), format!("parse {}", e.code.as_str()));
                    continue;
                }
            };
            for (name, r) in super::typeck3::typecheck_file3(&asset_catalog(), &file) {
                let v = match r {
                    Ok(_) => "ok".to_string(),
                    Err(e) => format!("{} {}", e.code.as_str(), e.message),
                };
                outcome.insert(key(name), v);
            }
        }
    }
    for (k, v) in &outcome {
        eprintln!("{k:40} {v}");
    }
    let refused: BTreeMap<&str, &str> = outcome
        .iter()
        .filter(|(_, v)| *v != "ok")
        .map(|(k, v)| (k.as_str(), v.split_whitespace().next().unwrap()))
        .collect();
    // Each refusal is a finding (docs/spikes/gq3-typeck.md): `names` binds
    // an interface, which edition 2 refuses too (T1) although the asset calls
    // it unchanged; `mentions` runs `fuzzy` over `Doc.title`, which has no
    // full-text index (T27 in both editions); `weighted`'s second inner read
    // draws `$d` from `bm25($docs, …)` into a scope where the outer `$d` is
    // visible (T68), the core spike's F2 still present after the asset's
    // fix. The later-stage forms are refused at the layer the RFC names:
    // `@if_commit`/`returning` and `crm::` at parse, the catalogue edge and
    // `@budget(rows = …)` at type check.
    let want: BTreeMap<&str, &str> = [
        ("bounded (later stage)", "T80"),
        ("mentions", "T27"),
        ("names", "T1"),
        ("namespaced (later stage)", "parse"),
        ("retract (later stage)", "parse"),
        ("schema_types (later stage)", "T1"),
        ("weighted", "T68"),
    ]
    .into_iter()
    .collect();
    assert_eq!(refused, want);
    assert!(outcome.len() >= 50, "only {} declarations", outcome.len());
}

/// Edition 2's verdict on `src` against the asset's schema: `ok` or the code.
pub(crate) fn e2(src: &str) -> String {
    let file = match super::parser::parse_edition2(src) {
        Ok(f) => f,
        Err(e) => return format!("parse {}", e.code.as_str()),
    };
    let cat = asset_catalog();
    let super::ast::FileBody::Queries(qs) = file.body else {
        return "statement".into();
    };
    for q in &qs {
        if let Err(e) = super::typecheck::typecheck_query_decl(&cat, q) {
            return e
                .diagnostic()
                .map_or_else(|| e.to_string(), |d| d.code.as_str().to_string());
        }
    }
    "ok".into()
}


/// Every GQT case's schema with its `query` and `mutate` sections.
pub(crate) fn gqt_corpus() -> Vec<(String, String, Vec<String>)> {
    let root = super::parser3_tests::root();
    let mut files = Vec::new();
    for dir in ["crates/omnigraph-gqt/cases", "crates/omnigraph-gqt/cases_slow"] {
        collect(&root.join(dir), &mut files);
    }
    files.sort();
    let mut out = Vec::new();
    for f in files {
        let text = std::fs::read_to_string(&f).unwrap();
        let name = f.strip_prefix(&root).unwrap_or(&f).display().to_string();
        let mut schema = String::new();
        let mut queries = Vec::new();
        let mut section: Option<(String, String)> = None;
        let mut flush = |section: &mut Option<(String, String)>, schema: &mut String, queries: &mut Vec<String>| {
            if let Some((header, body)) = section.take() {
                match header.split_whitespace().next().unwrap_or("") {
                    "schema" => *schema = body,
                    "query" | "mutate" => queries.push(body),
                    _ => {}
                }
            }
        };
        for line in text.lines() {
            if let Some(header) = line.strip_prefix("--- ") {
                flush(&mut section, &mut schema, &mut queries);
                section = Some((header.to_string(), String::new()));
            } else if let Some((_, body)) = section.as_mut() {
                body.push_str(line);
                body.push('\n');
            }
        }
        flush(&mut section, &mut schema, &mut queries);
        if !schema.is_empty() && !queries.is_empty() {
            out.push((name, schema, queries));
        }
    }
    out
}

fn collect(dir: &std::path::Path, out: &mut Vec<std::path::PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for e in entries.flatten() {
        let p = e.path();
        if p.is_dir() {
            collect(&p, out);
        } else if p.extension().is_some_and(|x| x == "gqt") {
            out.push(p);
        }
    }
}

/// One declaration's verdict: `ok` or its code.
fn verdict<T, E: std::fmt::Display>(r: &Result<T, E>, code: impl Fn(&E) -> String) -> String {
    match r {
        Ok(_) => "ok".into(),
        Err(e) => code(e),
    }
}

/// Edition 2's verdict per declaration of `text`.
fn edition2_verdicts(cat: &Catalog, text: &str) -> Option<Vec<(String, String)>> {
    let file = super::parser::parse_edition2(text).ok()?;
    let qs = match file.body {
        super::ast::FileBody::Queries(qs) => qs,
        super::ast::FileBody::Explain(q) => vec![q],
        _ => return None,
    };
    Some(
        qs.iter()
            .map(|q| {
                let r = super::typecheck::typecheck_query_decl(cat, q);
                (
                    q.name.clone(),
                    verdict(&r, |e| {
                        e.diagnostic()
                            .map_or_else(|| "other".to_string(), |d| d.code.as_str().to_string())
                    }),
                )
            })
            .collect(),
    )
}

/// The edition-2 corpus checked by both editions. Without the edition line a
/// text is edition 2 and its result is unchanged (the dispatcher is the only
/// new code on that path, and `every_edition2_text_in_the_repository_parses_as_before`
/// pins it); here every text edition 3 also parses is type-checked by the
/// edition-3 checker too, and the two verdicts are compared per declaration.
/// Every disagreement is either a rule edition 3 changes (each named below)
/// or a defect of this checker; the pinned table is the evidence.
#[test]
fn the_edition2_corpus_type_checks_alike_in_both_editions() {
    let mut agree = 0usize;
    let mut total = 0usize;
    let mut differ: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut e2_ok = 0usize;
    for (case, schema, queries) in gqt_corpus() {
        let Ok(schema) = parse_schema(&schema) else {
            continue;
        };
        let Ok(cat) = build_catalog(&schema) else {
            continue;
        };
        for text in queries {
            if super::parser3::edition_line(&text) != super::parser3::EditionLine::Absent {
                continue;
            }
            let Some(two) = edition2_verdicts(&cat, &text) else {
                continue;
            };
            let Ok(file) = parse_query3(&format!("gq 3;\n{text}")) else {
                continue;
            };
            let three: Vec<(String, String)> = super::typeck3::typecheck_file3(&cat, &file)
                .into_iter()
                .map(|(n, r)| (n, verdict(&r, |e| e.code.as_str().to_string())))
                .collect();
            for ((name, a), (_, b)) in two.iter().zip(&three) {
                total += 1;
                e2_ok += usize::from(a == "ok");
                if a == b {
                    agree += 1;
                } else {
                    differ
                        .entry(format!("edition 2 {a:>4} / edition 3 {b:>4}"))
                        .or_default()
                        .push(format!("{case} `{name}`"));
                }
            }
        }
    }
    eprintln!("{total} declarations, {e2_ok} accepted by edition 2, {agree} with the same verdict");
    for (k, v) in &differ {
        eprintln!("  {k}: {}", v.len());
        for x in v.iter().take(if std::env::var("GQ3_ALL").is_ok() { 1000 } else { 4 }) {
            eprintln!("      {x}");
        }
    }
    // The verdicts that change acceptance, pinned. Every row is a rule
    // edition 3 changes, a finding of docs/spikes/gq3-typeck.md:
    // - T65 (156), T71 (4): the rewriter's ranking and positional-option rows.
    // - T68 (5): an inner binding of an outer name, which edition 2 reads as
    //   a filter on the outer row; no rewriter row covers it (finding 4).
    // - T5 (4): a block naming a variable bound later in the scope now
    //   correlates on it (finding 5).
    // - T76 (1): two different upserts of one key in one write (finding 6).
    // - T42 (3): an order key over grouped rows that is neither a key nor an
    //   aggregate item (edition 2 admits a meta-field there).
    // - edition 3 accepting: T40 lifted (1); a traversal with no anchored
    //   endpoint, which edition 2 refuses as unexecutable and the core reads
    //   as a Correlate (4); an order key that is not a return item (1); a
    //   Boolean or property item beside aggregates (2).
    let mut changed: BTreeMap<String, usize> = BTreeMap::new();
    for (k, v) in &differ {
        let ok2 = k.contains("edition 2   ok");
        let ok3 = k.ends_with("edition 3   ok");
        if ok2 != ok3 {
            *changed.entry(k.clone()).or_default() += v.len();
        }
    }
    let want: BTreeMap<String, usize> = [
        ("edition 2   ok / edition 3   T5", 4),
        ("edition 2   ok / edition 3  T42", 3),
        ("edition 2   ok / edition 3  T65", 156),
        ("edition 2   ok / edition 3  T68", 5),
        ("edition 2   ok / edition 3  T71", 4),
        ("edition 2   ok / edition 3  T76", 1),
        ("edition 2   T5 / edition 3   ok", 4),
        ("edition 2   T9 / edition 3   ok", 1),
        ("edition 2  T40 / edition 3   ok", 1),
        ("edition 2  T42 / edition 3   ok", 1),
        ("edition 2  T58 / edition 3   ok", 1),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect();
    assert_eq!(changed, want);
    assert!(agree * 100 >= total * 88, "{agree} of {total}");
}

/// Finding 5: a block correlates on a variable the enclosing scope binds
/// after it. Edition 2 reads `$x` in the block as local (the GQT case
/// `issue_659_compiler_scopes.gqt` pins that); the RFC's "a filter or block
/// waits for every variable it names" correlates it. With both of one type,
/// the text is accepted by both and means two things.
#[test]
fn a_block_naming_a_later_variable_changes_meaning_silently() {
    let src = "query q() { match { $p: Person  not { $p knows $x }  $x: Person { handle: \"q\" } } return { $p } }";
    assert_eq!(e2(src), "ok");
    let c = ok(src);
    let text = format!("{:?}", read_of(&c).term);
    assert!(
        text.contains("Env([Col { name: \"p\"") && text.contains("Col { name: \"x\", ty: Node"),
        "the block's Env carries both `p` and `x`: {text}"
    );
}

/// Finding 4: edition 2 reads a block's binding of an outer variable as a
/// filter on the outer row (`not { $p: Person { name: "bob" } }` is "`$p` is
/// not bob"); edition 3 refuses it, and the rewriter has no row for it.
#[test]
fn an_inner_rebinding_that_edition_2_reads_as_a_filter() {
    let src = "query q() { match { $p: Person  not { $p: Person { handle: \"bob\" } } } return { $p } }";
    assert_eq!(e2(src), "ok");
    refused(src, "T68");
    // The mechanical rewrite keeps the meaning.
    ok("query q() { match { $p: Person  not { $p.handle = \"bob\" } } return { $p } }");
}


// ---------------------------------------------------------------------------
// Section 10, "Type-check tests", one test each.

fn cols(c: &CoreDecl) -> Vec<(String, String)> {
    read_of(c)
        .result
        .cols
        .iter()
        .map(|c| (c.name.clone(), c.ty.display()))
        .collect()
}

#[test]
fn t68_on_a_collision_with_each_kind_of_name_in_scope() {
    // A node binding, an edge binding, a `let` value, a `let` relation, a
    // column drawn by an earlier `from`, a parameter, and a ranking's own
    // output column.
    for (kind, src) in [
        ("node binding", "query q() { match { $c: Company  from { match { $x: Company } return { $x as c } } } return { $c } }"),
        ("edge binding", "query q() { match { $p $k:worksAt $c  from { match { $x: Company } return { $x as k } } } return { $p } }"),
        ("let value", "query q() { match { $p: Person  let $n = 1  from { match { $x: Company } return { $x as n } } } return { $p } }"),
        ("let relation", "query q() { match { let $r = { match { $x: Company } return { $x } }  from { match { $y: Company } return { $y as r } } } return { $y } }"),
        ("from column", "query q() { match { from { match { $x: Company } return { $x } }  from { match { $y: Company } return { $y as x } } } return { $x } }"),
        ("parameter", "query q($h: String) { match { from { match { $p: Person } return { $p.handle as h } } } return { $h } }"),
        ("ranking output", "query q($v: Vector(3)) { match { from distance({ match { $d: Doc } return { $d, $d.year as distance } }, $d.embedding, $v) } return { $d } }"),
        ("let reusing a column", "query q() { match { $p: Person  let $p = 1 } return { $p } }"),
    ] {
        let m = refused(src, "T68");
        assert!(!m.is_empty(), "{kind}");
    }
    // An inner binding that reuses an outer name: it would silently become a
    // filter on the outer row.
    let m = refused(
        "query q() { match { $p: Person  from { match { $p: Person  $p knows $q } return { $q } } } return { $p, $q } }",
        "T68",
    );
    assert!(m.contains("silently become a filter"), "{m}");
    // A `from` inside an inner read drawing a name the enclosing scope holds:
    // the asset's `weighted` (finding 3).
    refused(
        "query q($v: Vector(3)) { match { let $docs = { match { $d: Doc } return { $d } }  $d: Doc  from { match { from distance($docs, $d.embedding, $v) } return { $d as e } } } return { $e } }",
        "T68",
    );
    // The fix the code prints, an alias on the inner return, is accepted.
    ok("query q() { match { $c: Company  from { match { $x: Company } return { $x as c2 } } } return { $c, $c2 } }");
    // A traversal inside an inner read that names an outer variable is a
    // correlation, not a binding.
    ok("query q() { match { $p: Person  from { match { $p authored $d } return { $d } order { $d.year desc } limit 3 } } return { $p.name, $d.title } }");
}

#[test]
fn t69_both_ways() {
    // A relation where a scalar is read.
    for src in [
        "query q() { match { let $r = { match { $p: Person } return { $p } }  $r = 1 } return { 1 as one } }",
        "query q() { match { let $r = { match { $p: Person } return { $p } }  $x: Person } return { $r } }",
        "query c($h: String) { match { $p: Person { handle: $h } } return { $p } }\nquery q() { match { $x: Person  c($x.handle) } return { $x } }",
        "query q() { match { $x: Person  union({ match { $a: Person } return { $a } }, { match { $b: Person } return { $b as a } }) } return { $x } }",
    ] {
        let m = refused(src, "T69");
        assert!(m.contains("relation"), "{m}");
    }
    // A scalar where a relation is read.
    for src in [
        "query q() { match { $p: Person  from $p } return { $p } }",
        "query q() { match { let $v = 1  from $v } return { $v } }",
        "query q($h: String) { match { from $h } return { 1 as one } }",
        "query c($h: String) { match { $p: Person { handle: $h } } return { $p } }\nquery q() { match { from c({ match { $x: Person } return { $x.handle as h } }) } return { $p } }",
    ] {
        refused(src, "T69");
    }
}

/// Recursive reads in the asset's schema.
pub(crate) const REACHABLE: &str = "query reachable($from: String) {
  match {
    from union(
      { match { $p: Person { handle: $from }  $p knows $q } return { $q } },
      { match { from reachable($from)  $q knows $r } return { $r as q } })
  }
  return distinct { $q }
}";

#[test]
fn t70_for_a_non_monotone_cycle_and_a_budget_overrun() {
    // A monotone self-call compiles to `Fix`.
    let r = ok(REACHABLE);
    assert!(matches!(read_of(&r).term, Term::Fix { ref name, .. } if name == "reachable"));
    // `exists` over the recursive rows is admitted, and is the term of
    // `count { } > 0`.
    for form in ["exists { from members($from)  $m knows $q }", "count { from members($from)  $m knows $q } > 0"] {
        ok(&format!(
            "query members($from: String) {{ match {{ from union(
               {{ match {{ $p: Person {{ handle: $from }} }} return {{ $p as m }} }},
               {{ match {{ $q: Person  {form} }} return {{ $q as m }} }}) }} return distinct {{ $m }} }}"
        ));
    }
    // A `not` over the recursive read.
    let m = refused(
        "query bad($from: String) { match { from union(
           { match { $p: Person { handle: $from } } return { $p as m } },
           { match { $q: Person  not { from bad($from)  $m knows $q } } return { $q as m } }) } return distinct { $m } }",
        "T70",
    );
    assert!(m.contains("not") && m.contains("bad"), "{m}");
    // A `count` over it.
    let m = refused(
        "query bad($from: String) { match { from union(
           { match { $p: Person { handle: $from } } return { $p as m } },
           { match { $q: Person  count { from bad($from)  $m knows $q } > 2 } return { $q as m } }) } return distinct { $m } }",
        "T70",
    );
    assert!(m.contains("count"), "{m}");
    // An invented value without `min`.
    let m = refused(
        "query dist($from: String) @budget(rounds = 50) { match { from union(
           { match { $p: Person { handle: $from }  $p knows $q } return { $q, 1 as d } },
           { match { from dist($from)  $q knows $r } return { $r as q, $d + 1 as d } }) } return distinct { $q, $d } }",
        "T70",
    );
    assert!(m.contains("computed column") || m.contains("invent"), "{m}");
    // An invented value under `min` without a round budget.
    let m = refused(
        "query dist($from: String) { match { from union(
           { match { $p: Person { handle: $from }  $p knows $q } return { $q, 1 as d } },
           { match { from dist($from)  $q knows $r } return { $r as q, $d + 1 as d } }) } return { $q, min($d) as d } }",
        "T70",
    );
    assert!(m.contains("round budget"), "{m}");
    // ... and with one, the RFC's `distance` example compiles.
    let d = ok("query dist($from: String) @budget(rounds = 50) { match { from union(
           { match { $p: Person { handle: $from }  $p knows $q } return { $q, 1 as d } },
           { match { from dist($from)  $q knows $r } return { $r as q, $d + 1 as d } }) } return { $q, min($d) as d } }");
    assert!(matches!(read_of(&d).term, Term::Fix { rounds: Some(50), ref lattice, .. } if lattice == &vec!["d".to_string()]));
    // A filter on the `min` column inside the cycle.
    let m = refused(
        "query dist($from: String) @budget(rounds = 50) { match { from union(
           { match { $p: Person { handle: $from }  $p knows $q } return { $q, 1 as d } },
           { match { from dist($from)  $d < 3  $q knows $r } return { $r as q, $d + 1 as d } }) } return { $q, max($d) as d } }",
        "T70",
    );
    assert!(m.contains("filter reads the `min`/`max` column"), "{m}");
    // An order and a limit over the recursive rows.
    let m = refused(
        "query cut($from: String) { match { from union(
           { match { $p: Person { handle: $from } } return { $p as q } },
           { match { from { match { from cut($from) } return { $q } order { $q.handle } limit 1 }  $q knows $r } return { $r as q } }) } return distinct { $q } }",
        "T70",
    );
    assert!(m.contains("order") || m.contains("limit"), "{m}");
    // A computed recursive argument.
    let m = refused(
        "query down($n: I64) { match { from union(
           { match { $p: Person } return { $p } },
           { match { from down($n - 1) } return { $p } }) } return distinct { $p } }",
        "T70",
    );
    assert!(m.contains("computed argument"), "{m}");
    // A recursion with no base case.
    refused("query loop($h: String) { match { from loop($h) } return distinct { $q } }", "T70");
    // A recursion through two declared reads.
    let two = check(
        "query a($from: String) { match { from union(
           { match { $p: Person { handle: $from } } return { $p as q } },
           { match { from b($from)  $q knows $r } return { $r as q } }) } return distinct { $q } }
         query b($x: String) { match { from a($x) } return distinct { $q } }",
    );
    assert!(two.iter().all(|(_, r)| r.is_ok()), "{two:?}");
    // A budget overrun names the chain.
    let m = refused(
        "query leaf() { match { $p: Person } return { $p } }
         query mid() { match { from union(leaf(), leaf()) } return { $p } }
         query top() @budget(plan = 20) { match { from union(mid(), mid()) } return { $p } }",
        "T70",
    );
    assert!(m.contains("plan budget") && m.contains("mid → leaf"), "{m}");
}

#[test]
fn a_relation_function_over_zero_and_two_node_columns() {
    // Zero node columns: the ranked column is not one.
    let m = refused(
        "query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d.title as t } }, $d.embedding, $v, k: 3) } return { $t } }",
        "T79",
    );
    assert!(m.contains("none"), "{m}");
    // A scalar column named as the ranked one.
    refused(
        "query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d.title as d } }, $d.embedding, $v, k: 3) } return { $d } }",
        "T79",
    );
    // Two node columns: the call names which (the RFC's `passages` shape);
    // the other rides as payload.
    let c = ok(
        "query q($s: String) { match { from bm25({ match { $p authored $d } return { $p, $d } }, $d.body, $s) } return { $p.name, $d.title, $score } }",
    );
    assert_eq!(
        cols(&c),
        vec![
            ("p.name".to_string(), "String".to_string()),
            ("d.title".to_string(), "String".to_string()),
            ("score".to_string(), "F64".to_string()),
        ]
    );
    // A bare field name: the fix is the property access.
    let e = check("query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d } }, embedding, $v, k: 3) } return { $d } }");
    let e = e[0].1.as_ref().unwrap_err();
    assert_eq!(e.fix.as_deref(), Some("$d.embedding"));
}

#[test]
fn a_ranked_column_whose_property_is_not_declared() {
    refused(
        "query q($s: String) { match { from bm25({ match { $d: Doc } return { $d } }, $d.abstract, $s) } return { $d } }",
        "T27",
    );
    refused(
        "query q($v: Vector(3)) { match { from distance({ match { $d: Doc } return { $d } }, $d.vec, $v) } return { $d } }",
        "T15",
    );
    // A declared property without a full-text index.
    refused(
        "query q($s: String) { match { from bm25({ match { $d: Doc } return { $d } }, $d.title, $s) } return { $d } }",
        "T27",
    );
    // A query vector of the wrong dimension.
    refused(
        "query q() { match { from distance({ match { $d: Doc } return { $d } }, $d.embedding, [1.0, 2.0]) } return { $d } }",
        "T15",
    );
}

#[test]
fn nearest_without_k() {
    let m = refused(
        "query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v) } return { $d } }",
        "T17",
    );
    assert!(m.contains("law 3"), "{m}");
    refused(
        "query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 0) } return { $d } }",
        "T71",
    );
    refused(
        "query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 5, recall: 1.5) } return { $d } }",
        "T71",
    );
    refused(
        "query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 5, candidates: 50) } return { $d } }",
        "T71",
    );
    // `k` from a parameter is a window, never `limit`'s value.
    ok("query q($v: Vector(3), $k: I64) { match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: $k) } return { $d.title, $distance } limit 3 }");
}

#[test]
fn t65_with_the_from_fix() {
    for (src, fix) in [
        ("query q($v: Vector(3)) { match { $d: Doc } return { $d.title } order { nearest($d.embedding, $v) } limit 3 }", "from nearest("),
        ("query q($s: String) { match { $d: Doc } return { $d.title, bm25($d.body, $s) as s } }", "from bm25("),
        ("query q($s: String, $v: Vector(3)) { match { $d: Doc } return { $d.title } order { rrf(nearest($d.embedding, $v), bm25($d.body, $s), 60) } limit 3 }", "from rrf("),
        ("query q($v: Vector(3)) { match { $d: Doc  distance($d.embedding, $v) < 1.0 } return { $d } }", "from distance("),
    ] {
        let e = check(src);
        let e = e[0].1.as_ref().unwrap_err();
        assert_eq!(e.code.as_str(), "T65", "{src}");
        assert!(e.fix.as_deref().unwrap().starts_with(fix), "{e:?}");
    }
}

#[test]
fn t40_lifted() {
    // A block value compares with any scalar in scope.
    let src = "query full_collections() { match { $x: Collection  count { $d \"in\" $x } >= $x.min_docs } return { $x.slug } }";
    ok(src);
    assert_eq!(e2(src), "T40");
    // It is a value: bound, returned and ordered.
    let c = ok("query degrees($min: I64) { match { $p: Person  let $degree = count { $p knows $q }  $degree >= $min } return { $p.name, $degree } order { $degree desc } }");
    assert_eq!(cols(&c)[1], ("degree".to_string(), "I64".to_string()));
    // `sum` over an empty block is null; `count` is not.
    let c = ok("query s() { match { $p: Person  let $y = sum($d.year) { $p authored $d } } return { $y } }");
    assert_eq!(cols(&c)[0].1, "F64?");
    // A block that reads no outer variable stays refused, as today.
    refused("query q() { match { $p: Person  count { $d: Doc } > 1 } return { $p } }", "T39");
}

#[test]
fn optional_served() {
    let c = ok("query q() { match { $p: Person  optional { $p worksAt $c } } return { $p.name, $c.name, $c } }");
    assert_eq!(
        cols(&c),
        vec![
            ("p.name".to_string(), "String".to_string()),
            ("c.name".to_string(), "String?".to_string()),
            ("c".to_string(), "node Company?".to_string()),
        ]
    );
    let mut ops = Vec::new();
    read_of(&c).term.operators(&mut ops);
    assert!(ops.contains(&"Union"), "{ops:?}");
}

#[test]
fn if_commit_and_valid_are_refused_with_the_writes_rfc_named() {
    for a in ["@if_commit($h)", "@valid($h)"] {
        let e = parse_query3(&format!("gq 3;\nquery r($h: String) {a} {{ $p: Person {{ handle: $h }}  delete $p }}")).unwrap_err();
        assert_eq!(e.code.as_str(), "Q012");
        assert!(e.message.contains("writes RFC"), "{e}");
    }
}

/// Section 10 says "an unknown annotation retained"; section 5 and the
/// freeze list now refuse every name but the metadata allow-list (finding
/// 15): the test follows section 5.
#[test]
fn an_unknown_annotation_is_refused_and_metadata_is_retained() {
    ok("query q() @description(\"d\") @example(\"e\") @rename_from(\"old\") { match { $p: Person } return { $p } }");
    let m = refused("query q() @cache(ttl = 5) { match { $p: Person } return { $p } }", "T80");
    assert!(m.contains("@description"), "{m}");
    ok("query q() @budget(plan = 100) { match { $p: Person } return { $p } }");
    let m = refused("query q() @budget(rows = 10) { match { $p: Person } return { $p } }", "T80");
    assert!(m.contains("stage (N)"), "{m}");
}

// ---------------------------------------------------------------------------
// The rest of the brief's typing rules.

#[test]
fn arithmetic_widens_and_propagates_null() {
    let ty = |e: &str| {
        let c = ok(&format!("query q($i: I32, $l: I64, $f: F32, $g: F64, $u: U32) {{ match {{ $p: Person }} return {{ {e} as v }} }}"));
        cols(&c)[0].1.clone()
    };
    assert_eq!(ty("$i + $i"), "I32");
    assert_eq!(ty("$i + $l"), "I64");
    assert_eq!(ty("$i * 2"), "I32", "an integer literal takes the other operand's width");
    assert_eq!(ty("$i + $f"), "F64");
    assert_eq!(ty("$f * $f"), "F32");
    assert_eq!(ty("$f + $g"), "F64");
    assert_eq!(ty("$f * 0.5"), "F64", "a float literal is F64, so it widens an F32");
    assert_eq!(ty("$p.age + 1"), "I32?", "nullable when an operand is");
    assert_eq!(ty("-$l % 3"), "I64");
    assert_eq!(ty("$u + $i"), "I64", "mixed signedness: the RFC states no rule");
    for e in ["$p.name + 1", "$p + 1", "-$p.name", "$p.age + true"] {
        refused(&format!("query q() {{ match {{ $p: Person }} return {{ {e} as v }} }}"), "T72");
    }
    refused("query q($u: U64, $i: I32) { match { $p: Person } return { $u + $i as v } }", "T72");
    // The term carries `can_fail`, so the planner keeps it behind its guards.
    let c = ok("query q() { match { $p: Person  $p.age * 2 >= 10 } return { $p } }");
    fn find_filter(t: &Term) -> Option<&SExpr> {
        match t {
            Term::Filter { pred, .. } => Some(pred),
            Term::Project { input, .. } | Term::Order { input, .. } | Term::Cut { input, .. } => find_filter(input),
            _ => None,
        }
    }
    assert!(find_filter(&read_of(&c).term).unwrap().can_fail);
}

#[test]
fn the_edge_binding_scans_an_edge_type() {
    let c = ok("query q($h: String) { match { $k: WorksAt  $k.@src = $h } return { $k.@dst } }");
    let mut ops = Vec::new();
    read_of(&c).term.operators(&mut ops);
    assert_eq!(ops[..3], ["Project", "Project", "Filter"]);
    assert!(matches!(&read_of(&c).term, Term::Project { input, .. }
        if matches!(input.as_ref(), Term::Project { input, .. }
            if matches!(input.as_ref(), Term::Filter { input, .. }
                if matches!(input.as_ref(), Term::Scan { ty: Ty::Edge { .. }, .. })))));
}

#[test]
fn the_ranking_functions_type_their_result_columns() {
    let c = ok("query q($v: Vector(3)) { match { from distance({ match { $d: Doc } return { $d } }, $d.embedding, $v) } return { $d, $distance } }");
    assert_eq!(cols(&c)[1], ("distance".into(), "F32".into()));
    let c = ok("query q($v: Vector(3)) { match { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $v, k: 5, recall: 0.9) } return { $distance } }");
    assert_eq!(cols(&c)[0], ("distance".into(), "F32".into()));
    assert!(format!("{:?}", read_of(&c).term).contains("recall: Some(0.9)"));
    let c = ok("query q($s: String) { match { from bm25({ match { $d: Doc } return { $d } }, $d.body, terms($s, mode: all, max_edits: 1)) } return { $score } }");
    assert_eq!(cols(&c)[0], ("score".into(), "F64".into()));
    // `rrf` yields the target and `score` only: the population's other
    // columns are gone after fusion.
    let fused = "let $docs = { match { $p authored $d } return { $p, $d } }
                 from rrf(nearest($docs, $d.embedding, $v, k: 50), bm25($docs, $d.body, $s), k: 60, weights: [2.0, 1.0])";
    let c = ok(&format!("query q($s: String, $v: Vector(3)) {{ match {{ {fused} }} return {{ $d.title, $score }} }}"));
    assert_eq!(cols(&c), vec![("d.title".into(), "String".into()), ("score".into(), "F64".into())]);
    refused(&format!("query q($s: String, $v: Vector(3)) {{ match {{ {fused} }} return {{ $p.name }} }}"), "T6");
    // ... and a correlated `from` over the population draws them again.
    ok(&format!("query q($s: String, $v: Vector(3)) {{ match {{ {fused}  from {{ match {{ from $docs  $d2: Doc  $d2 = $d }} return {{ $p }} }} }} return {{ $p.name, $score }} }}").replace("from $docs  $d2: Doc  $d2 = $d", "$p authored $d"));
}

#[test]
fn rrf_refusals() {
    let pop = "let $docs = { match { $d: Doc } return { $d } }";
    for (opts, arms) in [
        ("", "nearest($docs, $d.embedding, $v, k: 5), nearest($docs, $d.embedding, $v, k: 5)"),
        ("", "nearest($docs, $d.embedding, $v, k: 5)"),
        (", weights: [1.0]", "nearest($docs, $d.embedding, $v, k: 5), bm25($docs, $d.body, $s)"),
        ("", "nearest($docs, $d.embedding, $v, k: 5), { match { $p: Person } return { $p } }"),
        ("", "nearest($docs, $d.embedding, $v, k: 5), { match { $p: Person } return { $p } order { $p.age } }"),
    ] {
        refused(&format!("query q($s: String, $v: Vector(3)) {{ match {{ {pop}  from rrf({arms}{opts}) }} return {{ $d }} }}"), "T75");
    }
    for opts in [", k: 0", ", weights: [1.0, 0.0]", ", depth: 2"] {
        refused(&format!("query q($s: String, $v: Vector(3)) {{ match {{ {pop}  from rrf(nearest($docs, $d.embedding, $v, k: 5), bm25($docs, $d.body, $s){opts}) }} return {{ $d }} }}"), "T71");
    }
    // Two arms over one population with different queries are distinct arms;
    // an ordered inline read is an arm.
    ok(&format!("query q($s: String, $t: String, $v: Vector(3)) {{ match {{ {pop}  from rrf(bm25($docs, $d.body, $s), bm25($docs, $d.body, $t), {{ match {{ $d: Doc }} return {{ $d }} order {{ $d.year desc }} limit 10 }}) }} return {{ $d, $score }} }}"));
}

#[test]
fn union_agrees_by_position_and_widens_nullability() {
    let c = ok("query q() { match { from union({ match { $p: Person } return { $p.city as c } }, { match { $x: Company } return { $x.name as c } }) } return { $c } }");
    assert_eq!(cols(&c)[0], ("c".into(), "String?".into()));
    let m = refused("query q() { match { from union({ match { $p: Person } return { $p.age as c } }, { match { $x: Company } return { $x.name as c } }) } return { $c } }", "T73");
    assert!(m.contains("arm 2 column 1"), "{m}");
    refused("query q() { match { from union({ match { $p: Person } return { $p } }, { match { $x: Company } return { $x as p } }) } return { $p } }", "T73");
    refused("query q() { match { from union({ match { $p: Person } return { $p, $p.age as a } }, { match { $x: Person } return { $x as p } }) } return { $p } }", "T73");
    // A flat union and a nested one are one term (the formatter flattens).
    let flat = ok("query q() { match { from union({ match { $p: Person } return { $p } }, { match { $q: Person } return { $q as p } }, { match { $r: Person } return { $r as p } }) } return { $p } }");
    let nested = ok("query q() { match { from union(union({ match { $p: Person } return { $p } }, { match { $q: Person } return { $q as p } }), { match { $r: Person } return { $r as p } }) } return { $p } }");
    assert_eq!(read_of(&flat).term, read_of(&nested).term);
}

#[test]
fn a_call_binds_its_arguments_and_never_substitutes() {
    // `friends_of`'s body binds `$p`; the caller passes `$p.handle` from a
    // scope that also binds `$p`. Substitution would make the body's binding
    // `$p: Person { handle: $p.handle }`, every person; binding keeps the
    // body closed.
    let unit = "query friends_of($h: String) { match { $p: Person { handle: $h }  $p knows $q } return { $q } }
                query q() { match { $p: Person { handle: \"erin\" }  from friends_of($p.handle) } return { $q } }";
    let all = check(unit);
    let CoreDecl::Read(r) = &all[1].1.as_ref().unwrap().core else { panic!() };
    let text = format!("{:?}", r.term);
    assert!(text.contains("Call { name: \"friends_of\", bindings: [(\"h\", SExpr { kind: Prop { col: \"p\", prop: \"handle\" }"), "{text}");
    assert!(text.contains("Param(\"h\")"), "the body reads its parameter, not the argument: {text}");
    // A declared read may not shadow a registry name or a clause keyword.
    refused("query nearest() { match { $p: Person } return { $p } }", "T74");
    refused("query count() { match { $p: Person } return { $p } }", "T74");
    // A read cannot draw from a write (A7), and a write calls no declared
    // read until stage W2.
    refused("query w($h: String) { insert Person { handle: $h, name: $h } }\nquery q() { match { from w(\"a\") } return { 1 as one } }", "T74");
    let m = refused("query r() { match { $p: Person } return { $p } }\nquery w() { from r()  update $p set { city: null } }", "T74");
    assert!(m.contains("W2"), "{m}");
    // Arity and argument types.
    refused("query f($h: String) { match { $p: Person { handle: $h } } return { $p } }\nquery q() { match { from f() } return { $p } }", "T7");
    refused("query f($h: String) { match { $p: Person { handle: $h } } return { $p } }\nquery q() { match { from f(3) } return { $p } }", "T7");
    // A default fills an omitted trailing argument.
    ok("query f($h: String, $n: I64 = 3) { match { $p: Person { handle: $h } } return { $p } limit $n }\nquery q() { match { from f(\"a\") } return { $p } }");
}

#[test]
fn a_column_drawn_from_must_be_variable_shaped() {
    let e = check("query q() { match { from { match { $p: Person } return { $p.name } } } return { 1 as one } }");
    let e = e[0].1.as_ref().unwrap_err();
    assert_eq!(e.code.as_str(), "T74");
    assert!(e.message.contains("`p.name`") && e.fix.as_deref().unwrap().contains("as p_name"), "{e:?}");
}

// ---------------------------------------------------------------------------
// Writes.

fn write_of(c: &CoreDecl) -> &CoreWrite {
    match c {
        CoreDecl::Write(w) => w,
        CoreDecl::Read(_) => panic!("a read"),
    }
}

#[test]
fn write_groups_type_their_sinks() {
    let w = ok("query close_company($slug: String) { $c: Company { slug: $slug }  $p $k:worksAt $c  delete $k }");
    let w = write_of(&w);
    assert_eq!(w.class, WriteClass::Destructive);
    assert!(matches!(&w.groups[0].sinks[0], Sink::Delete { var, target } if var == "k" && target.is_edge()));
    let w = ok("query relocate($h: String) { $p: Person { handle: $h }  update $p set { city: null, age: $p.age + 1 } }");
    let w = write_of(&w);
    assert!(matches!(&w.groups[0].sinks[0], Sink::Update { assignments, .. } if assignments[0].1 == AssignValue::Null));
    // `insert` with no clauses runs once over the one empty row.
    let w = ok("query hire($h: String, $slug: String) { insert Person { handle: $h, name: $h }  insert WorksAt { from: $h, to: $slug } }");
    assert_eq!(write_of(&w).groups[0].input, Term::Unit);
    // An endpoint by bound reference, and the mismatch.
    ok("query link($a: String, $b: String) { $x: Person { handle: $a }  $y: Person { handle: $b }  insert Knows { from: $x, to: $y } }");
    let m = refused("query link($a: String, $b: String) { $x: Person { handle: $a }  $d: Doc { id: $b }  insert Knows { from: $x, to: $d } }", "T78");
    assert!(m.contains("`to` of `Knows` is a `Person`") && m.contains("node Doc"), "{m}");
    // A write fed by a ranked read, a `let` and an `optional`.
    ok("query tag_near($q: Vector(3)) { from nearest({ match { $d: Doc } return { $d } }, $d.embedding, $q, k: 20)  update $d set { tags: [\"near\"] } }");
    ok("query w($h: String) { $p: Person { handle: $h }  let $n = count { $p knows $q }  optional { $p worksAt $c }  $n > 1  update $p set { city: \"busy\" } }");
    // Groups: a later group sees the earlier ones' effects; each has its own
    // clauses.
    let w = ok("query two($h: String) { insert Person { handle: $h, name: $h }  $p: Person { handle: $h }  update $p set { city: \"x\" } }");
    assert_eq!(write_of(&w).groups.len(), 2);
    // `null` only where the property is nullable.
    refused("query w($h: String) { $p: Person { handle: $h }  update $p set { name: null } }", "T3");
    // A statement's variable must be a bound reference.
    refused("query w() { update $p set { city: null } }", "T77");
    refused("query w() { $p: Person  let $n = 1  delete $n }", "T77");
    // Update of an edge is refused, as today (T16).
    refused("query w() { $p $k:worksAt $c  update $k set { since: 1 } }", "T16");
}

#[test]
fn the_constructive_or_destructive_split_and_the_conflict_refusal() {
    let m = refused("query retire($h: String) { $p: Person { handle: $h }  $p $k:worksAt $c  delete $k  update $p set { city: null } }", "T76");
    assert!(m.contains("constructive-or-destructive"), "{m}");
    // Split across groups too: the split is per write.
    refused("query w($h: String) { $p: Person { handle: $h }  delete $p  insert Person { handle: $h, name: $h } }", "T76");
    // Two different values for one entity and property.
    let m = refused("query w($h: String) { $p: Person { handle: $h }  update $p set { city: \"a\" }  update $p set { city: \"b\" } }", "T76");
    assert!(m.contains("two values to `city`"), "{m}");
    // Identical effects apply once.
    ok("query w($h: String) { $p: Person { handle: $h }  update $p set { city: \"a\" }  update $p set { city: \"a\" } }");
    // Two different upserts of one key.
    refused("query w() { insert Person { handle: \"a\", name: \"x\" }  insert Person { handle: \"a\", name: \"y\" } }", "T76");
    ok("query w() { insert Person { handle: \"a\", name: \"x\" }  insert Person { handle: \"a\", name: \"x\" } }");
}

#[test]
fn as_commit_is_admitted_only_on_a_write_body() {
    let w = ok("query retire($h: String) as commit { $p: Person { handle: $h }  $p $k:worksAt $c  delete $k }");
    assert_eq!(write_of(&w).write_mode, super::ast::MutationWriteMode::AsCommit);
    let e = parse_query3("gq 3;\nquery q() as commit { match { $p: Person } return { $p } }").unwrap_err();
    assert_eq!(e.code.as_str(), "Q011");
}

/// The probes `docs/spikes/gq3-typeck/compare_core.py` runs through the core
/// spike's typer, written here as edition-3 text; the script holds these
/// verdicts and prints where the two disagree.
#[test]
fn the_core_spike_probes_in_rust() {
    let dist = |ret: &str, budget: &str, filter: &str| {
        format!(
            "query dist($from: String) {budget} {{ match {{ from union(
               {{ match {{ $p: Person {{ handle: $from }}  $p knows $q }} return {{ $q, 1 as d }} }},
               {{ match {{ from dist($from) {filter} $q knows $r }} return {{ $r as q, $d + 1 as d }} }}) }} return {{ {ret} }} }}"
        )
    };
    let probes: Vec<(&str, String, &str)> = vec![
        ("inner_rebinding_in_not", "query x() { match { $p: Person  not { $p: Person { handle: \"bob\" } } } return { $p.name } }".into(), "T68"),
        ("future_name_in_block", "query x() { match { $p: Person  not { $p knows $x }  $x: Person { handle: \"q\" } } return { $p.name } }".into(), "ok"),
        ("from_collision", "query x() { match { $c: Company  from { match { $x: Company } return { $x as c } } } return { $c } }".into(), "T68"),
        ("union_mismatch", "query x() { match { from union({ match { $p: Person } return { $p.age as c } }, { match { $x: Company } return { $x.name as c } }) } return { $c } }".into(), "T73"),
        ("union_nullable", "query x() { match { from union({ match { $p: Person } return { $p.city as c } }, { match { $x: Company } return { $x.name as c } }) } return { $c } }".into(), "ok"),
        ("not_over_recursion", "query bad($from: String) { match { from union({ match { $p: Person { handle: $from } } return { $p as m } }, { match { $q: Person  not { from bad($from)  $m knows $q } } return { $q as m } }) } return distinct { $m } }".into(), "T70"),
        ("exists_over_recursion", "query members($from: String) { match { from union({ match { $p: Person { handle: $from } } return { $p as m } }, { match { $q: Person  exists { from members($from)  $m knows $q } } return { $q as m } }) } return distinct { $m } }".into(), "ok"),
        ("computed_recursive_arg", "query down($n: I64) { match { from union({ match { $p: Person } return { $p } }, { match { from down($n - 1) } return { $p } }) } return distinct { $p } }".into(), "T70"),
        ("invented_value_without_min", dist("$q, $d", "@budget(rounds = 50)", "").replace("return { $q, $d }", "return distinct { $q, $d }"), "T70"),
        ("min_without_rounds", dist("$q, min($d) as d", "", ""), "T70"),
        ("min_with_rounds", dist("$q, min($d) as d", "@budget(rounds = 50)", ""), "ok"),
        ("filter_on_max_column", dist("$q, max($d) as d", "@budget(rounds = 50)", " $d < 3 "), "T70"),
        ("age_times_two_into_age", "query w($h: String) { $p: Person { handle: $h }  update $p set { age: $p.age * 2 } }".into(), "ok"),
        ("update_and_delete_one_write", "query w($h: String) { $p: Person { handle: $h }  $p $k:worksAt $c  delete $k  update $p set { city: null } }".into(), "T76"),
    ];
    for (name, src, want) in probes {
        assert_eq!(code(&src), want, "{name}\n{src}");
    }
}
