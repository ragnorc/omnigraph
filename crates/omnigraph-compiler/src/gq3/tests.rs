//! SPIKE tests for RFC PR #1202: every freeze-list row, every slot, the
//! diagnostics, the RFC's literal pest rules where they clash, and the
//! later capabilities the RFC and the other open RFCs name.

use pest::Parser;

use super::{Gq3Parser, Rule};
use crate::catalog::{Catalog, build_catalog};
use crate::query::ast::*;
use crate::query::diagnostic::QueryDiagnostic;
use crate::query::parser::parse_query_diagnostic;
use crate::query::typecheck::typecheck_query_decl;
use crate::schema::parser::parse_schema;

pub const SCHEMA: &str = r#"
node Person {
    name: String @key
    age: I32?
    tags: [String]?
    bio: String? @index
    embedding: Vector(3)? @index
}
node Company { name: String @key }
node Doc {
    slug: String @key
    text: String @index
    title: String? @index
    embedding: Vector(3) @index
    lex: String?
}
edge Knows: Person -> Person
edge WorksAt: Person -> Company
edge Owns: Person -> Doc
edge OwnedBy: Doc -> Person
"#;

pub fn catalog() -> Catalog {
    build_catalog(&parse_schema(SCHEMA).unwrap()).unwrap()
}

/// Parse and type-check every declaration; the first diagnostic.
pub fn check(src: &str) -> Result<QueryFile, QueryDiagnostic> {
    let file = parse_query_diagnostic(src)?;
    let catalog = catalog();
    let decls: Vec<QueryDecl> = match &file.body {
        FileBody::Queries(d) => d.clone(),
        FileBody::Explain(d) => vec![d.clone()],
        _ => Vec::new(),
    };
    for d in &decls {
        typecheck_query_decl(&catalog, d).map_err(|e| {
            e.diagnostic().cloned().unwrap_or_else(|| {
                QueryDiagnostic::typecheck(crate::query::codes::T6, e.to_string())
            })
        })?;
    }
    Ok(file)
}

fn code(src: &str) -> String {
    match check(src) {
        Ok(_) => "ok".to_string(),
        Err(d) => d.code.as_str().to_string(),
    }
}

fn err(src: &str) -> QueryDiagnostic {
    check(src).expect_err(src)
}

fn ok(src: &str) {
    if let Err(d) = check(src) {
        panic!(
            "expected ok:\n{src}\n=> {} {} fix={:?}",
            d.code, d.message, d.fix
        );
    }
}

fn parses(rule: Rule, src: &str) -> bool {
    Gq3Parser::parse(rule, src).is_ok_and(|mut p| p.next().is_some_and(|p| p.as_str() == src))
}

// ---------------------------------------------------------------------------
// The edition line

#[test]
fn edition_line_selects_the_grammar() {
    ok("gq 3;\nquery q() { match { $p: Person } return { $p.name } }");
    ok("gq 2;\nquery q() { match { $p: Person $p.name contains \"A\" } return { $p.name } }");
    // No line: edition 2 in the spike (the RFC makes the default 3 at release).
    ok("query q() { match { $p: Person $p.name contains \"A\" } return { $p.name } }");
    assert_eq!(
        code("gq 4;\nquery q() { match { $p: Person } return { $p.name } }"),
        "Q006"
    );
    assert_eq!(
        code("gq 3;\nquery q() { match { $p: Person } return { $p.name } }\ngq 3;"),
        "Q007"
    );
    // Settings follow the edition line and are read by the edition-2 rules.
    let file = check("gq 3;\nset traversal_work_limit = 1000;\nquery q() { match { $p: Person } return { $p.name } }").unwrap();
    assert_eq!(file.settings.len(), 1);
    // Statements are unchanged.
    assert!(matches!(
        check("gq 3;\nbranch create \"x\" from main").unwrap().body,
        FileBody::Branch(_)
    ));
    assert!(matches!(
        check("gq 3;\nbranch list").unwrap().body,
        FileBody::Branch(_)
    ));
    assert!(matches!(
        check("gq 3;\nexplain query q() { match { $p: Person } return { $p.name } }")
            .unwrap()
            .body,
        FileBody::Explain(_)
    ));
}

#[test]
fn missing_parameter_list_keeps_its_diagnostic() {
    let d = err("gq 3;\nquery q { match { $p: Person } return { $p.name } }");
    assert_eq!(d.code.as_str(), "Q002");
    assert_eq!(d.fix.as_deref(), Some("write `query q()`"));
    let d = err("gq 3;\nmutation m { insert Person { name: \"a\" } }");
    assert_eq!(d.code.as_str(), "Q002");
}

// ---------------------------------------------------------------------------
// Freeze-list rows that work

#[test]
fn mutation_keyword_and_the_wrong_keyword_refusal() {
    ok("gq 3;\nmutation m($n: String) { insert Person { name: $n } }");
    ok("gq 3;\nmutation m($n: String) as commit { update Person set { age: 3 } where name = $n }");
    let d = err("gq 3;\nquery m($n: String) { insert Person { name: $n } }");
    assert_eq!(d.code.as_str(), "T62");
    let s = d.suggestion.unwrap();
    assert_eq!(s.edits[0].replacement, "mutation");
    let d = err("gq 3;\nmutation m() { match { $p: Person } return { $p.name } }");
    assert_eq!(d.code.as_str(), "T62");
}

#[test]
fn null_is_legal_only_as_an_assignment_value() {
    ok("gq 3;\nmutation m($n: String) { update Person set { age: null } where name = $n }");
    ok("gq 3;\nmutation m($n: String) { insert Person { name: $n, age: null } }");
    // A read filter with null keeps today's parse error.
    assert_eq!(
        code("gq 3;\nquery q() { match { $p: Person $p.age = null } return { $p.name } }"),
        "Q001"
    );
    // The RFC also admits null in `prop_match`, which is a read binding:
    // `$p: Person { age: null }` would be a null literal in a read.
    assert_eq!(
        code("gq 3;\nquery q() { match { $p: Person { age: null } } return { $p.name } }"),
        "Q001"
    );
}

#[test]
fn retrieve_binds_typed_arms_and_order_reads_them() {
    ok(
        "gq 3;\nquery q($v: Vector(3)) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec as score } order { $d.vec } limit 3 }",
    );
    ok(
        "gq 3;\nquery q($t: String) { match { $d: Doc } retrieve { $d.rank: bm25($d.text, $t) } return { $d.slug, $d.rank as score } order { $d.rank desc } limit 3 }",
    );
    ok(
        "gq 3;\nquery q($v: Vector(3), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v), $d.rank: bm25($d.text, $t) } return { $d.slug } order { rrf($d.vec, $d.rank, k: 60) } limit 3 }",
    );
    // asc on bm25 is T54, as today.
    assert_eq!(
        code(
            "gq 3;\nquery q($t: String) { match { $d: Doc } retrieve { $d.rank: bm25($d.text, $t) } return { $d.slug } order { $d.rank asc } limit 3 }"
        ),
        "T54"
    );
    // nearest needs a limit (T17), as today.
    assert_eq!(
        code(
            "gq 3;\nquery q($v: Vector(3)) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug } order { $d.vec } }"
        ),
        "T17"
    );
}

#[test]
fn arm_refusals() {
    // T63: the arm shadows a declared property (`Doc.lex`).
    assert_eq!(
        code(
            "gq 3;\nquery q($t: String) { match { $d: Doc } retrieve { $d.lex: bm25($d.text, $t) } return { $d.slug } order { $d.lex desc } limit 3 }"
        ),
        "T63"
    );
    // T64: an arm read in `match`.
    assert_eq!(
        code(
            "gq 3;\nquery q($t: String) { match { $d: Doc $d.rank > 1.0 } retrieve { $d.rank: bm25($d.text, $t) } return { $d.slug } order { $d.rank desc } limit 3 }"
        ),
        "T64"
    );
    // T65: a ranking written in `order` (the edition-2 spelling).
    let d = err(
        "gq 3;\nquery q($v: Vector(3)) { match { $d: Doc } return { $d.slug } order { nearest($d.embedding, $v) } limit 3 }",
    );
    assert_eq!(d.code.as_str(), "T65");
    // T65: an arm `order` does not read.
    assert_eq!(
        code(
            "gq 3;\nquery q($v: Vector(3)) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug } order { $d.slug } limit 3 }"
        ),
        "T65"
    );
}

#[test]
fn calls_resolve_against_the_registry() {
    ok(
        "gq 3;\nquery q() { match { $p: Person contains($p.name, \"A\") starts_with($p.name, \"A\") } return { $p.name } }",
    );
    ok("gq 3;\nquery q() { match { $p: Person \"rust\" in $p.tags } return { $p.name } }");
    ok(
        "gq 3;\nquery q($t: String) { match { $d: Doc search($d.text, $t) fuzzy($d.text, $t, max_edits: 1) match_text($d.text, $t) } return { $d.slug } }",
    );
    ok(
        "gq 3;\nquery q() { match { $p: Person $p.age > 3 } return { count($p) as n, max($p.age) as oldest, count(distinct $p.age) as ages } }",
    );
    ok(
        "gq 3;\nquery q() { match { $p: Person } return { $p.name, now() as t, date(\"2026-01-01\") as d } }",
    );
    // The infix spelling is a parse error whose fix prints the call.
    let d =
        err("gq 3;\nquery q() { match { $p: Person $p.name contains \"A\" } return { $p.name } }");
    assert_eq!(d.code.as_str(), "Q009");
    let fix = d.fix.unwrap();
    assert!(fix.contains("contains($p.name, \"A\")"), "{fix}");
    // Positional options are refused with the named form as the fix.
    let d = err(
        "gq 3;\nquery q($v: Vector(3), $t: String) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v), $d.rank: bm25($d.text, $t) } return { $d.slug } order { rrf($d.vec, $d.rank, 60) } limit 3 }",
    );
    assert_eq!(d.code.as_str(), "T61");
    assert_eq!(d.fix.as_deref(), Some("pass k: 60 by name"));
    let d = err(
        "gq 3;\nquery q($t: String) { match { $d: Doc fuzzy($d.text, $t, 2) } return { $d.slug } }",
    );
    assert_eq!(d.code.as_str(), "T61");
    assert_eq!(d.fix.as_deref(), Some("pass max_edits: 2 by name"));
    assert_eq!(
        code("gq 3;\nquery q() { match { $p: Person lower($p.name) = \"a\" } return { $p.name } }"),
        "T66"
    );
    assert_eq!(
        code(
            "gq 3;\nquery q($t: String) { match { $d: Doc search($d.text, $t, mode: 1) } return { $d.slug } }"
        ),
        "T61"
    );
}

#[test]
fn a_new_function_is_a_registry_row_not_a_grammar_rule() {
    // `norm`, `lower`, `terms`, `phrase`, `rerank`: the grammar already parses every one.
    for src in [
        "lower($p.name) = \"a\"",
        "terms($q, mode: all, max_edits: 1)",
        "phrase($d.text, $q, slop: 2)",
        "has_any($p.tags, [\"a\", \"b\"])",
        "regex_match($p.name, \"^A\")",
        "rerank($d.text, $q, model: \"m\", k: 50)",
        "norm($d.vec)",
    ] {
        assert!(parses(Rule::expr, src), "{src}");
    }
    // A row added to a registry resolves with no grammar change.
    let rows = [super::registry::Row::__spike_lower()];
    let e = super::registry::resolve_in(&rows, "lower", vec![Expr::Variable("x".into())], vec![])
        .unwrap();
    assert!(matches!(e, Expr::Variable(_)));
}

#[test]
fn limit_takes_a_parameter() {
    let file = check("gq 3;\nquery q($v: Vector(3), $k: I64) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug } order { $d.vec } limit $k }").unwrap();
    let FileBody::Queries(d) = file.body else {
        panic!()
    };
    assert_eq!(d[0].gq3.as_ref().unwrap().limit_param.as_deref(), Some("k"));
    // Parameter defaults parse (binding them is not implemented in the spike).
    ok("gq 3;\nquery q($k: I64 = 5) { match { $p: Person } return { $p.name } limit $k }");
}

#[test]
fn having_filters_above_the_aggregate_and_over_arms() {
    ok(
        "gq 3;\nquery q() { match { $p: Person $p knows $f } return { $p.name, count($f) as friends } having { count($f) > 1 } order { friends desc } }",
    );
    ok(
        "gq 3;\nquery q() { match { $p: Person $p knows $f } return { $p.name, count($f) as friends } having { friends > 1 } }",
    );
    ok(
        "gq 3;\nquery q($v: Vector(3)) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.slug, $d.vec as dist } having { $d.vec < 0.6 } order { $d.vec } limit 3 }",
    );
    assert_eq!(
        code("gq 3;\nquery q() { match { $p: Person } return { $p.name } having { $p.age } }"),
        "T67"
    );
}

#[test]
fn annotations_are_generic() {
    ok(
        "gq 3;\nquery q() @description(\"d\") @instruction(\"i\") @cache(ttl = 60) @owner(\"team\") { match { $p: Person } return { $p.name } }",
    );
    for name in [
        "budget(rows = 10)",
        "approximate(recall = 0.9)",
        "satisfice",
        "brief(\"x\")",
        "phase(explore)",
    ] {
        let d = err(&format!(
            "gq 3;\nquery q() @{name} {{ match {{ $p: Person }} return {{ $p.name }} }}"
        ));
        assert_eq!(d.code.as_str(), "T60", "{name}");
    }
    // `@example` and `@if_commit` are "typed and working" in the RFC; nothing
    // in the compiler or engine serves them.
    assert_eq!(
        code("gq 3;\nmutation m($c: String) @if_commit($c) { insert Person { name: \"a\" } }"),
        "T60"
    );
    // The evaluation's `@budget(rows: 200000)` (call-option punctuation)
    // does not parse once annotation arguments use `=`, the `.pg` spelling.
    assert_eq!(
        code("gq 3;\nquery q() @budget(rows: 10) { match { $p: Person } return { $p.name } }"),
        "Q001"
    );
}

#[test]
fn every_contextual_keyword_stays_an_identifier() {
    let keywords = [
        "gq",
        "query",
        "mutation",
        "view",
        "batch",
        "match",
        "retrieve",
        "return",
        "returning",
        "having",
        "order",
        "limit",
        "optional",
        "exists",
        "distinct",
        "insert",
        "update",
        "delete",
        "where",
        "set",
        "reset",
        "show",
        "all",
        "as",
        "commit",
        "branch",
        "unattached",
        "on",
        "load",
        "csv",
        "explain",
        "asc",
        "desc",
        "contains",
        "starts_with",
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "now",
        "date",
        "datetime",
        "nearest",
        "bm25",
        "rrf",
        "search",
        "fuzzy",
        "match_text",
        "true_x",
        "node",
        "edge",
        "interface",
        "implements",
    ];
    for kw in keywords {
        // property after a dot, edge name in a traversal, return alias, bare
        // property in a mutation `where`
        for src in [
            format!(
                "gq 3;\nquery q() {{ match {{ $p: Person $p.{kw} = 1 }} return {{ $p.name }} }}"
            ),
            format!(
                "gq 3;\nquery q() {{ match {{ $p: Person $p {kw} $q }} return {{ $p.name }} }}"
            ),
            format!("gq 3;\nquery q() {{ match {{ $p: Person }} return {{ $p.name as {kw} }} }}"),
            format!("gq 3;\nmutation m() {{ delete Person where {kw} = 1 }}"),
        ] {
            assert!(parse_query_diagnostic(&src).is_ok(), "{kw}: {src}");
        }
    }
    for reserved in ["and", "or", "not", "is", "null", "in"] {
        assert!(
            parse_query_diagnostic(&format!(
                "gq 3;\nquery q() {{ match {{ $p: Person }} return {{ $p.name as {reserved} }} }}"
            ))
            .is_err()
        );
    }
}

// ---------------------------------------------------------------------------
// Slots: parse, then T60 naming the owner.

#[test]
fn every_slot_parses_and_refuses_with_its_owner() {
    let cases: &[(&str, &str, &str)] = &[
        (
            "view",
            "gq 3;\nview Contested($since: DateTime) @description(\"d\") { match { $a: Person } return { $a.name } }",
            "derived relations",
        ),
        (
            "batch",
            "gq 3;\nbatch explore @phase(explore) { query a() { match { $p: Person } return { $p.name } } }",
            "batch",
        ),
        (
            "optional",
            "gq 3;\nquery q() { match { $p: Person optional { $p worksAt $c } } return { $p.name } }",
            "derived relations",
        ),
        (
            "block value count",
            "gq 3;\nquery q() { match { $p: Person } return { $p.name, count { $p knows $f } as knows } }",
            "derived relations",
        ),
        (
            "block value exists",
            "gq 3;\nquery q() { match { $p: Person } return { $p.name, exists { $p worksAt $c } as employed } }",
            "derived relations",
        ),
        (
            "view binding",
            "gq 3;\nquery q() { match { $x: Contested(date(\"2026-01-01\")) { name: \"a\" } } return { $x.name } }",
            "derived relations",
        ),
        (
            "returning",
            "gq 3;\nmutation m() { insert Person { name: \"a\" } returning { name } }",
            "writes RFC",
        ),
        (
            "@type_id",
            "gq 3;\nquery q() { match { $p: Person } return { $p.@type_id } }",
            "polymorphic",
        ),
        (
            "@distance",
            "gq 3;\nquery q() { match { $p: Person } return { $p.@distance } }",
            "0048",
        ),
        (
            "arm meta-field",
            "gq 3;\nquery q($v: Vector(3)) { match { $d: Doc } retrieve { $d.vec: nearest($d.embedding, $v) } return { $d.vec.@distance } order { $d.vec } limit 2 }",
            "0048",
        ),
        (
            "catalogue type",
            "gq 3;\nquery q() { match { $t: @Type } return { $t.name } }",
            "catalogue",
        ),
        (
            "catalogue edge",
            "gq 3;\nquery q() { match { $t: @Operator  $t @property $p } return { $t.name } }",
            "catalogue",
        ),
        (
            "namespace",
            "gq 3;\nquery q() { match { $c: crm::Contact } return { $c.name } }",
            "namespaces",
        ),
        (
            "Branch param",
            "gq 3;\nquery q($b: Branch) { match { $p: Person } return { $p.name } }",
            "branch",
        ),
        (
            "Commit param",
            "gq 3;\nquery q($c: Commit?) { match { $p: Person } return { $p.name } }",
            "branch",
        ),
        (
            "schema decl in a query file",
            "gq 3;\nnode X { a: String }",
            "no RFC",
        ),
    ];
    for (name, src, owner) in cases {
        let d = err(src);
        assert_eq!(d.code.as_str(), "T60", "{name}: {} {}", d.code, d.message);
        assert!(
            d.fix.as_deref().unwrap_or("").contains(owner),
            "{name}: fix {:?}",
            d.fix
        );
    }
}

#[test]
fn an_interface_binding_is_t1_until_its_rfc() {
    let schema = "interface Named { name: String }\nnode P implements Named { name: String @key }";
    let catalog = build_catalog(&parse_schema(schema).unwrap()).unwrap();
    let file =
        parse_query_diagnostic("gq 3;\nquery q() { match { $x: Named } return { $x.name } }")
            .unwrap();
    let FileBody::Queries(d) = file.body else {
        panic!()
    };
    let e = typecheck_query_decl(&catalog, &d[0]).unwrap_err();
    assert_eq!(e.diagnostic().unwrap().code.as_str(), "T1");
}

// ---------------------------------------------------------------------------
// Traversal and arrows

#[test]
fn the_triple_is_unchanged_and_arrows_print_the_triple() {
    ok(
        "gq 3;\nquery q() { match { $p: Person $p knows $f $p <knows> $g $p $e:knows $h $p knows{1,2} $i } return { $p.name } }",
    );
    // The issue 659 shape: a directed alternation resolves per member.
    ok(
        "gq 3;\nset traversal_work_limit = 100000;\nquery q() { match { $p: Person $x: Person $p (owns | ownedBy) $d $p * $x } return { $p.name } }",
    );
    // `<e>` over opposed members is T22 today; the RFC narrows T22 so this
    // resolves per member (a semantic change the spike does not implement).
    assert_eq!(
        code(
            "gq 3;\nset traversal_work_limit = 100000;\nquery q() { match { $p: Person $p <owns | ownedBy> $d } return { $p.name } }"
        ),
        "T22"
    );
    for (src, fix) in [
        ("$p -knows-> $f", "$p knows $f"),
        ("$f <-knows- $p", "$p knows $f"),
        ("$p -knows- $f", "$p <knows> $f"),
        ("$p -$e:knows{1,2}-> $f", "$p $e:knows{1,2} $f"),
        ("$p -(knows | worksAt)-> $f", "$p (knows | worksAt) $f"),
    ] {
        let d = err(&format!(
            "gq 3;\nquery q() {{ match {{ $p: Person {src} }} return {{ $p.name }} }}"
        ));
        assert_eq!(d.code.as_str(), "Q008", "{src}");
        assert_eq!(
            d.fix.as_deref(),
            Some(format!("write `{fix}`").as_str()),
            "{src}"
        );
        assert!(d.position.is_some());
    }
}

#[test]
fn an_incoming_triple_is_refused_in_edition_3() {
    // Edition 2 accepts it (Direction::In); the RFC makes it `T5` with the
    // swapped clause as the fix. This is a break the RFC's compatibility
    // section does not list.
    ok("query q() { match { $c: Company $c worksAt $p } return { $c.name } }");
    let d = err("gq 3;\nquery q() { match { $c: Company $c worksAt $p } return { $c.name } }");
    assert_eq!(d.code.as_str(), "T68");
    assert_eq!(d.fix.as_deref(), Some("write `$p worksAt $c`"));
}

// ---------------------------------------------------------------------------
// The RFC's own pest rules, where they do not parse what it says.

#[test]
fn rfc_call_args_rule_cannot_parse_an_option_after_a_positional() {
    assert!(!parses(Rule::call_rfc, "rrf($d.a, $d.b, k: 60)"));
    assert!(parses(Rule::call, "rrf($d.a, $d.b, k: 60)"));
}

#[test]
fn rfc_binding_rule_swallows_a_parenthesised_filter() {
    // Edition-2 text: a type-only binding followed by a parenthesised filter.
    let src = "query q() { match { $p: Person\n ($p.age > 3 or $p.age < 1) } return { $p.name } }";
    assert!(crate::query::parser::parse_query_diagnostic_edition2(src).is_ok());
    // Under the RFC's `binding`, the filter is read as view arguments.
    assert!(parses(
        Rule::binding_rfc,
        "$p: Person\n ($p.age > 3 or $p.age < 1)"
    ));
    // The spike's tight form keeps it a filter.
    ok(&format!("gq 3;\n{src}"));
}

#[test]
fn rfc_undirected_rule_drops_the_unparenthesised_alternation() {
    assert!(!parses(Rule::undirected_edge_rfc, "<knows | likes>"));
    assert!(parses(Rule::undirected_edge, "<knows | likes>"));
}

#[test]
fn rfc_examples_that_do_not_parse_under_its_traversal_rule() {
    // Inline-typed endpoints (the Traversal section and the catalogue row).
    assert_eq!(
        code(
            "gq 3;\nquery q() { match { $p: Person <owns | ownedBy> $d: Doc } return { $p.name } }"
        ),
        "Q001"
    );
    // `$t: @Type @property $p: @Property` parses, as a binding, a bare
    // meta-field filter and a second binding: not a traversal.
    let file = parse_query_diagnostic(
        "gq 3;\nquery q() { match { $t: @Type @property $p: @Property } return { $t.name } }",
    )
    .unwrap();
    let FileBody::Queries(d) = file.body else {
        panic!()
    };
    assert_eq!(d[0].match_clause.len(), 3);
    assert!(matches!(d[0].match_clause[1], Clause::Filter(_)));
    // `$e: @Edge @from $t` parses, but as a binding, a bare meta-field filter
    // and a bare-variable filter: not a traversal.
    let file = parse_query_diagnostic(
        "gq 3;\nquery q() { match { $e: @Edge @from $t } return { $e.name } }",
    )
    .unwrap();
    let FileBody::Queries(d) = file.body else {
        panic!()
    };
    assert_eq!(d[0].match_clause.len(), 3);
    assert!(matches!(d[0].match_clause[0], Clause::Binding(_)));
    assert!(matches!(d[0].match_clause[1], Clause::Filter(_)));
}

#[test]
fn every_pg_file_in_the_repository_parses_under_the_one_grammar() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut files = Vec::new();
    collect(&root, "pg", &mut files);
    let mut failures = Vec::new();
    let mut count = 0;
    for f in &files {
        let text = std::fs::read_to_string(f).unwrap();
        if parse_schema(&text).is_err() {
            continue; // not a valid edition-2 schema either
        }
        count += 1;
        if Gq3Parser::parse(Rule::file, &text).is_err() {
            failures.push(f.display().to_string());
        }
    }
    eprintln!(
        "pg files parsed by the one grammar: {} of {count}",
        count - failures.len()
    );
    assert!(failures.is_empty(), "{failures:#?}");
}

pub fn collect(dir: &std::path::Path, ext: &str, out: &mut Vec<std::path::PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for e in entries.flatten() {
        let p = e.path();
        let name = p.file_name().and_then(|n| n.to_str()).unwrap_or("");
        if p.is_dir() {
            if matches!(name, "target" | ".git" | "node_modules") {
                continue;
            }
            collect(&p, ext, out);
        } else if p.extension().and_then(|x| x.to_str()) == Some(ext) {
            out.push(p);
        }
    }
}

// ---------------------------------------------------------------------------
// Fuzz: random token streams never panic either front end, and any text both
// editions accept, with none of the removed spellings, means the same.

#[test]
fn random_token_streams() {
    const TOKENS: &[&str] = &[
        "gq",
        "3",
        ";",
        "query",
        "mutation",
        "q",
        "(",
        ")",
        "{",
        "}",
        "match",
        "return",
        "retrieve",
        "having",
        "order",
        "limit",
        "$p",
        "$f",
        "$d",
        ":",
        "Person",
        "Doc",
        "knows",
        "<",
        ">",
        "-",
        "->",
        "<-",
        ".",
        "name",
        "age",
        "=",
        ">=",
        "and",
        "or",
        "not",
        "is",
        "null",
        "in",
        ",",
        "as",
        "count",
        "exists",
        "optional",
        "contains",
        "nearest",
        "bm25",
        "rrf",
        "k",
        "@",
        "@id",
        "\"a\"",
        "1",
        "2.5",
        "*",
        "|",
        "insert",
        "update",
        "delete",
        "set",
        "where",
        "view",
        "batch",
        "desc",
        "asc",
        "distinct",
        "::",
        "crm",
        "[",
        "]",
        "$v",
        "embedding",
        "text",
        "@Type",
    ];
    let mut seed: u64 = 0x9E37_79B9_7F4A_7C15;
    let mut next = || {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        seed
    };
    let catalog = catalog();
    let (mut both, mut agreed) = (0, 0);
    for _ in 0..20_000 {
        let len = (next() % 40) as usize + 1;
        let mut src = String::from("query q() { match { $p: Person ");
        for _ in 0..len {
            src.push_str(TOKENS[(next() % TOKENS.len() as u64) as usize]);
            src.push(' ');
        }
        src.push_str("} return { $p.name } }");
        let v3 = format!("gq 3;\n{src}");
        let a = std::panic::catch_unwind(|| {
            crate::query::parser::parse_query_diagnostic_edition2(&src)
        });
        let b = std::panic::catch_unwind(|| parse_query_diagnostic(&v3));
        let (Ok(a), Ok(b)) = (a, b) else {
            panic!("panic on {src}")
        };
        if let (Ok(a), Ok(b)) = (a, b) {
            both += 1;
            let (FileBody::Queries(a), FileBody::Queries(b)) = (a.body, b.body) else {
                continue;
            };
            let refused = b[0].gq3.as_ref().is_some_and(|x| !x.refusals.is_empty());
            if refused {
                continue;
            }
            let ia = super::migrate::compile_ir(&a[0], &catalog);
            let ib = super::migrate::compile_ir(&b[0], &catalog);
            if ia.is_ok() || ib.is_ok() {
                assert_eq!(ia, ib, "same text, different meaning:\n{src}");
                agreed += 1;
            }
        }
    }
    eprintln!("fuzz: {both} texts parsed by both editions; {agreed} compiled, all to equal IR");
}

/// Differential fuzz over well-formed reads built from today's constructs:
/// each text is written in edition 2, migrated, and both are compiled; the
/// typed IR must be equal whenever edition 2 compiles.
#[test]
fn generated_reads_mean_the_same_in_both_editions() {
    let mut seed: u64 = 0xD1B5_4A32_D192_ED03;
    let mut next = move |n: u64| {
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        seed % n
    };
    let catalog = catalog();
    let (mut compiled, mut equal, mut refused_v3) = (0, 0, Vec::new());
    for _ in 0..3000 {
        let mut clauses = vec!["$p: Person".to_string()];
        for _ in 0..next(4) {
            clauses.push(match next(10) {
                0 => "$p knows $f".into(),
                1 => "$p <knows> $g".into(),
                2 => "$p $e:knows $h".into(),
                3 => "$p.age > 3".into(),
                4 => "$p.name contains \"A\"".into(),
                5 => "$p.name starts_with \"A\" or $p.age is null".into(),
                6 => "not { $p worksAt $c }".into(),
                7 => "count { $p knows $z } >= 1".into(),
                8 => "\"x\" in $p.tags".into(),
                _ => "$p.tags contains \"x\"".into(),
            });
        }
        let ranked = next(4);
        let (params, order, limit) = match ranked {
            0 => (
                "$v: Vector(3)",
                "order { nearest($p.embedding, $v) }".to_string(),
                " limit 3",
            ),
            1 => (
                "$t: String",
                "order { bm25($p.bio, $t) desc }".to_string(),
                " limit 3",
            ),
            2 => (
                "$v: Vector(3), $t: String",
                format!(
                    "order {{ rrf(nearest($p.embedding, $v), bm25($p.bio, $t){}) }}",
                    if next(2) == 0 { ", 60" } else { "" }
                ),
                " limit 3",
            ),
            _ => (
                "",
                if next(2) == 0 {
                    "order { $p.name desc }".into()
                } else {
                    String::new()
                },
                if next(2) == 0 { " limit 5" } else { "" },
            ),
        };
        let ret = match (ranked, next(3)) {
            (0, 0) => "$p.name, nearest($p.embedding, $v) as d",
            (1, 0) => "$p.name, bm25($p.bio, $t) as s",
            (_, 1) => "$p.name, $p.age",
            _ => "$p.name",
        };
        let src = format!(
            "query q({params}) {{ match {{ {} }} return {{ {ret} }} {order}{limit} }}",
            clauses.join("\n ")
        );
        let Ok(old) = crate::query::parser::parse_query_diagnostic_edition2(&src) else {
            continue;
        };
        let FileBody::Queries(decls) = &old.body else {
            continue;
        };
        if super::migrate::compile_ir(&decls[0], &catalog).is_err() {
            continue;
        }
        compiled += 1;
        let m = super::migrate::migrate(&src, Some(&catalog), Default::default()).unwrap();
        match &m.proof[0].1 {
            super::migrate::Proof::Equal => equal += 1,
            other => refused_v3.push(format!("{src}\n  -> {}\n  => {other:?}", m.text)),
        }
        // Idempotent.
        let again = super::migrate::migrate(&m.text, Some(&catalog), Default::default()).unwrap();
        assert_eq!(again.text, m.text);
    }
    eprintln!(
        "generated: {compiled} compiled in edition 2; {equal} proved equal after migration; {} not",
        refused_v3.len()
    );
    for r in refused_v3.iter().take(5) {
        eprintln!("{r}");
    }
    assert!(refused_v3.is_empty());
}

#[test]
fn rfc_rules_refuse_todays_date_lists_and_distinct_aggregates() {
    assert!(!parses(
        Rule::list_lit_rfc,
        "[date(\"2026-01-01\"), date(\"2026-02-01\")]"
    ));
    assert!(parses(
        Rule::list_lit,
        "[date(\"2026-01-01\"), date(\"2026-02-01\")]"
    ));
    assert!(!parses(Rule::count_call_rfc, "sum(distinct $p.age)"));
    // With the amended rule the type checker keeps today's code.
    assert_eq!(
        code("gq 3;\nquery q() { match { $p: Person } return { sum(distinct $p.age) as t } }"),
        "T57"
    );
    ok(
        "gq 3;\nquery q() { match { $p: Person date(\"2026-01-01\") in [date(\"2026-02-01\"), date(\"2026-01-01\")] } return { $p.name } }",
    );
}

#[test]
fn swapping_an_incoming_triple_keeps_the_typed_ir() {
    // The review expected the swap to change the expand's direction and so
    // fail an IR-equality proof. Lowering orients each expand from the side
    // already bound, so both spellings lower to the same IR.
    let catalog = catalog();
    for (two, three) in [
        (
            "query q() { match { $c: Company $c worksAt $p } return { $p.name } }",
            "gq 3;\nquery q() { match { $c: Company $p worksAt $c } return { $p.name } }",
        ),
        (
            "query q() { match { $c: Company not { $c worksAt $p } } return { $c.name } }",
            "gq 3;\nquery q() { match { $c: Company not { $p worksAt $c } } return { $c.name } }",
        ),
        (
            "query q() { match { $p: Person $c: Company $c worksAt $p } return { $p.name } }",
            "gq 3;\nquery q() { match { $p: Person $c: Company $p worksAt $c } return { $p.name } }",
        ),
        (
            "query q() { match { $c worksAt $p $c: Company } return { $p.name } }",
            "gq 3;\nquery q() { match { $p worksAt $c $c: Company } return { $p.name } }",
        ),
    ] {
        let a = parse_query_diagnostic(two).unwrap();
        let b = parse_query_diagnostic(three).unwrap();
        let (FileBody::Queries(a), FileBody::Queries(b)) = (a.body, b.body) else {
            panic!()
        };
        let ia = super::migrate::compile_ir(&a[0], &catalog).unwrap();
        let ib = super::migrate::compile_ir(&b[0], &catalog).unwrap();
        if ia != ib {
            eprintln!("swap changes IR:\n{two}\n{ia}\n{ib}");
        }
        eprintln!("{} : {}", if ia == ib { "equal" } else { "DIFFERS" }, two);
        assert_eq!(ia, ib);
    }
    // Negative control: on a same-type edge the position is the meaning, and
    // the comparison sees the difference.
    let a = parse_query_diagnostic(
        "query q() { match { $p: Person { name: \"a\" } $p knows $f } return { $f.name } }",
    )
    .unwrap();
    let b = parse_query_diagnostic(
        "query q() { match { $p: Person { name: \"a\" } $f knows $p } return { $f.name } }",
    )
    .unwrap();
    let (FileBody::Queries(a), FileBody::Queries(b)) = (a.body, b.body) else {
        panic!()
    };
    assert_ne!(
        super::migrate::compile_ir(&a[0], &catalog),
        super::migrate::compile_ir(&b[0], &catalog)
    );
}
