//! SPIKE: the one-break claim. Each later capability the RFC, its evaluation
//! and the open RFCs name, written as the query it would need, run against
//! the edition-3 front end. Writes `target/spike-gq3/later.tsv`.
//!
//! Outcomes:
//! - `slot`  parses and is refused with T60 (or T1 for an interface): the
//!   owning RFC fills it with no grammar change.
//! - `row`   parses and is refused as an unknown function, option or arity
//!   (T66/T61): a registry row, no grammar change.
//! - `works` parses and type-checks today.
//! - `new`   does not parse: needs a new production. Additive when no text
//!   the frozen grammar accepts changes meaning (argued per row in the report).
//! - `misparse` parses, as something else.

use super::tests::check;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Out {
    Slot,
    Row,
    Works,
    New,
    Misparse,
}

fn classify(src: &str) -> (Out, String) {
    match check(src) {
        Ok(_) => (Out::Works, "ok".into()),
        Err(d) => {
            let code = d.code.as_str().to_string();
            let out = match code.as_str() {
                "T60" | "T1" => Out::Slot,
                "T66" | "T61" => Out::Row,
                c if c.starts_with('Q') => Out::New,
                _ => Out::Misparse,
            };
            (out, format!("{code}: {}", d.message))
        }
    }
}

const Q: &str = "gq 3;\nset traversal_work_limit = 100000;\n";

#[test]
fn later_capabilities_against_the_frozen_grammar() {
    use Out::*;
    // (owner, capability, query body, expected outcome)
    let cases: Vec<(&str, &str, String, Out)> = vec![
        // ---- derived relations, optional patterns, closure
        (
            "derived",
            "view declaration",
            format!(
                "{Q}view Busy($n: I64) {{ match {{ $p: Person count {{ $p knows $f }} >= $n }} return {{ $p.name }} }}"
            ),
            Slot,
        ),
        (
            "derived",
            "view binding with arguments",
            format!("{Q}query q() {{ match {{ $x: Busy(2) }} return {{ $x.name }} }}"),
            Slot,
        ),
        (
            "derived",
            "optional pattern",
            format!(
                "{Q}query q() {{ match {{ $p: Person optional {{ $p worksAt $c }} }} return {{ $p.name, $c.name }} }}"
            ),
            Slot,
        ),
        (
            "derived",
            "block aggregate as a value",
            format!(
                "{Q}query q() {{ match {{ $p: Person }} return {{ $p.name, count {{ $p knows $f }} as friends }} }}"
            ),
            Slot,
        ),
        (
            "derived",
            "post-group filter",
            format!(
                "{Q}query q() {{ match {{ $p: Person $p knows $f }} return {{ $p.name, count($f) as n }} having {{ n > 1 }} }}"
            ),
            Works,
        ),
        (
            "derived",
            "collect()",
            format!(
                "{Q}query q() {{ match {{ $p: Person $p knows $f }} return {{ $p.name, collect($f.name) as names }} }}"
            ),
            Row,
        ),
        (
            "derived",
            "partial object in return",
            format!("{Q}query q() {{ match {{ $p: Person }} return {{ $p {{ name, age }} }} }}"),
            New,
        ),
        (
            "derived",
            "collect of a partial object",
            format!(
                "{Q}query q() {{ match {{ $p: Person $p knows $f }} return {{ collect($f {{ name }}) as fs }} }}"
            ),
            New,
        ),
        (
            "derived",
            "top-N per group",
            format!(
                "{Q}query q() {{ match {{ $p: Person $p knows $f }} return {{ $p.name, $f.name }} order {{ $f.name }} limit 3 per $p }}"
            ),
            New,
        ),
        // ---- retrieval (RFC 0048 / PR #793, evaluation §9.5)
        (
            "0048",
            "candidates option on an arm",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v, candidates: 200) }} return {{ $d.slug }} order {{ $d.vec }} limit 3 }}"
            ),
            Row,
        ),
        (
            "0048",
            "exact search option",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v, exact: true) }} return {{ $d.slug }} order {{ $d.vec }} limit 3 }}"
            ),
            Row,
        ),
        (
            "0048",
            "three rrf arms",
            format!(
                "{Q}query q($v: Vector(3), $t: String) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v), $d.a: bm25($d.text, $t), $d.b: bm25($d.title, $t) }} return {{ $d.slug }} order {{ rrf($d.vec, $d.a, $d.b) }} limit 3 }}"
            ),
            Row,
        ),
        (
            "0048",
            "rrf weights as an option",
            format!(
                "{Q}query q($v: Vector(3), $t: String) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v), $d.a: bm25($d.text, $t) }} return {{ $d.slug }} order {{ rrf($d.vec, $d.a, weights: [2.0, 1.0]) }} limit 3 }}"
            ),
            Row,
        ),
        (
            "0048",
            "per-arm weight (PR #793's spelling)",
            format!(
                "{Q}query q($v: Vector(3), $t: String) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v, weight: 2.0), $d.a: bm25($d.text, $t) }} return {{ $d.slug }} order {{ rrf($d.vec, $d.a) }} limit 3 }}"
            ),
            Row,
        ),
        (
            "0048",
            "score threshold as a value",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v) }} return {{ $d.slug, $d.vec as dist }} having {{ $d.vec < 0.4 }} order {{ $d.vec }} limit 3 }}"
            ),
            Works,
        ),
        (
            "0048",
            "norm() of an arm",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v) }} return {{ $d.slug, norm($d.vec) as n }} order {{ $d.vec }} limit 3 }}"
            ),
            Row,
        ),
        (
            "0048",
            "weighted sum of arms (arithmetic)",
            format!(
                "{Q}query q($v: Vector(3), $t: String) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v), $d.a: bm25($d.text, $t) }} return {{ 0.7 * norm($d.vec) + 0.3 * norm($d.a) as s }} order {{ s desc }} limit 3 }}"
            ),
            New,
        ),
        (
            "0048",
            "rerank as an arm",
            format!(
                "{Q}query q($t: String) {{ match {{ $d: Doc }} retrieve {{ $d.rr: rerank($d.text, $t, model: \"m\", k: 50) }} return {{ $d.slug }} order {{ $d.rr desc }} limit 3 }}"
            ),
            Row,
        ),
        (
            "eval",
            "rerank as a stage with a bare `k 50`",
            format!(
                "{Q}query q($t: String) {{ match {{ $d: Doc }} retrieve {{ $d.a: bm25($d.text, $t) rerank(\"m\", $d.text, k 50) }} return {{ $d.slug }} order {{ $d.a desc }} limit 3 }}"
            ),
            New,
        ),
        (
            "eval",
            "arm window as a bare `k 200` suffix",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v) k 200 }} return {{ $d.slug }} order {{ $d.vec }} limit 3 }}"
            ),
            New,
        ),
        (
            "1202",
            "arm meta-field `$d.vec.@distance`",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v) }} return {{ $d.vec.@distance }} order {{ $d.vec }} limit 3 }}"
            ),
            Slot,
        ),
        (
            "eval",
            "arm struct field `$a.vec.distance`",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v) }} return {{ $d.slug }} having {{ $d.vec.distance < 0.6 }} order {{ $d.vec }} limit 3 }}"
            ),
            Slot,
        ),
        // ---- lexical search (PR #792)
        (
            "792",
            "terms() query value",
            format!(
                "{Q}query q($t: String) {{ match {{ $d: Doc match_terms($d.text, terms($t, mode: all, max_edits: 1)) }} return {{ $d.slug }} }}"
            ),
            Row,
        ),
        (
            "792",
            "bm25 over terms() in an arm",
            format!(
                "{Q}query q($t: String) {{ match {{ $d: Doc }} retrieve {{ $d.a: bm25($d.text, terms($t)) }} return {{ $d.slug }} order {{ $d.a desc }} limit 3 }}"
            ),
            Row,
        ),
        (
            "792",
            "phrase search",
            format!(
                "{Q}query q($t: String) {{ match {{ $d: Doc phrase($d.text, $t, slop: 2) }} return {{ $d.slug }} }}"
            ),
            Row,
        ),
        (
            "792",
            "search/match_text/fuzzy, which PR #792 removes at the next major",
            format!(
                "{Q}query q($t: String) {{ match {{ $d: Doc search($d.text, $t) match_text($d.title, $t) fuzzy($d.text, $t, max_edits: 1) }} return {{ $d.slug }} }}"
            ),
            Works,
        ),
        // ---- writes (writes RFC, evaluation §9.6)
        (
            "writes",
            "returning",
            format!("{Q}mutation m() {{ insert Person {{ name: \"x\" }} returning {{ name }} }}"),
            Slot,
        ),
        (
            "writes",
            "pattern-scoped update",
            format!(
                "{Q}mutation m($n: String) {{ update $p: Person where {{ $p: Person {{ name: $n }} $p worksAt $c }} set {{ age: 1 }} }}"
            ),
            New,
        ),
        (
            "writes",
            "pattern-scoped delete of an edge",
            format!(
                "{Q}mutation m($n: String) {{ delete $e where {{ $p: Person {{ name: $n }} $p $e:knows $f }} }}"
            ),
            New,
        ),
        (
            "writes",
            "invalidate",
            format!("{Q}mutation m($n: String) {{ invalidate Person where name = $n }}"),
            New,
        ),
        (
            "writes",
            "computed assignment",
            format!(
                "{Q}mutation m($n: String) {{ update Person set {{ age: age + 1 }} where name = $n }}"
            ),
            New,
        ),
        (
            "writes",
            "commit precondition annotation",
            format!(
                "{Q}mutation m($c: String) @if_commit($c) {{ insert Person {{ name: \"x\" }} }}"
            ),
            Slot,
        ),
        (
            "writes/Lance",
            "insert-if-absent (WhenMatched::DoNothing)",
            format!("{Q}mutation m() {{ insert Person {{ name: \"x\" }} on conflict skip }}"),
            New,
        ),
        // ---- polymorphic types (PR #884)
        (
            "884",
            "interface binding",
            "interface Named { name: String }".to_string(),
            Works,
        ),
        (
            "884",
            "union binding",
            format!("{Q}query q() {{ match {{ $x: Person | Company }} return {{ $x.name }} }}"),
            New,
        ),
        (
            "884",
            "type test `is`",
            format!("{Q}query q() {{ match {{ $x: Person $x is Person }} return {{ $x.name }} }}"),
            New,
        ),
        (
            "884",
            "@type_id",
            format!("{Q}query q() {{ match {{ $x: Person }} return {{ $x.@type_id }} }}"),
            Slot,
        ),
        (
            "884",
            "typed endpoint value `Person($id)`",
            format!(
                "{Q}mutation m($id: String) {{ insert Knows {{ from: Person($id), to: $id }} }}"
            ),
            New,
        ),
        (
            "884",
            "wildcard binding `$x: *`",
            format!("{Q}query q() {{ match {{ $x: * }} return {{ $x.@type }} }}"),
            New,
        ),
        (
            "884",
            "type variable `$x: $t`",
            format!("{Q}query q() {{ match {{ $x: $t }} return {{ $x.@type }} }}"),
            New,
        ),
        // ---- schema namespaces (PR #1015)
        (
            "1015",
            "namespaced binding",
            format!("{Q}query q() {{ match {{ $c: crm::Contact }} return {{ $c.name }} }}"),
            Slot,
        ),
        (
            "1015",
            "namespaced edge",
            format!(
                "{Q}query q() {{ match {{ $p: Person $p crm::ownedBy $c }} return {{ $p.name }} }}"
            ),
            New,
        ),
        (
            "1015",
            "`use` line",
            format!(
                "gq 3;\nuse crm;\nquery q() {{ match {{ $c: Contact }} return {{ $c.name }} }}"
            ),
            New,
        ),
        (
            "1015",
            "namespace block",
            "gq 3;\nnamespace crm { node Contact { name: String } }".to_string(),
            New,
        ),
        // ---- catalogue
        (
            "catalogue",
            "catalogue type binding",
            format!("{Q}query q() {{ match {{ $t: @Type }} return {{ $t.name }} }}"),
            Slot,
        ),
        (
            "catalogue",
            "catalogue edge",
            format!(
                "{Q}query q() {{ match {{ $t: @Operator $t @property $p }} return {{ $t.name }} }}"
            ),
            Slot,
        ),
        (
            "catalogue",
            "rank search over a catalogue type",
            format!(
                "{Q}query q($q: String) {{ match {{ $t: @Type }} retrieve {{ $t.lex: bm25($t.instruction, $q) }} return {{ $t.name }} order {{ $t.lex desc }} limit 5 }}"
            ),
            Slot,
        ),
        // ---- branches and time
        (
            "branch",
            "Branch / Commit parameter kinds",
            format!(
                "{Q}query q($b: Branch, $c: Commit) {{ match {{ $p: Person }} return {{ $p.name }} }}"
            ),
            Slot,
        ),
        (
            "eval",
            "read target `at $commit` on a declaration",
            format!(
                "{Q}query q($c: Commit) at $c {{ match {{ $p: Person }} return {{ $p.name }} }}"
            ),
            New,
        ),
        (
            "eval",
            "`at date(…)` on a binding",
            format!(
                "{Q}query q() {{ match {{ $p: Person at date(\"2026-01-01\") }} return {{ $p.name }} }}"
            ),
            Misparse,
        ),
        // ---- agent-first batch and annotations
        (
            "batch",
            "batch of declarations",
            format!(
                "{Q}batch b @phase(explore) {{ query a() {{ match {{ $p: Person }} return {{ $p.name }} }} }}"
            ),
            Slot,
        ),
        (
            "batch",
            "@budget with `=`",
            format!(
                "{Q}query q() @budget(rows = 10, ms = 200) {{ match {{ $p: Person }} return {{ $p.name }} }}"
            ),
            Slot,
        ),
        (
            "eval",
            "@budget with `:` (the evaluation's spelling)",
            format!(
                "{Q}query q() @budget(rows: 10) {{ match {{ $p: Person }} return {{ $p.name }} }}"
            ),
            New,
        ),
        (
            "eval",
            "an unknown annotation is metadata",
            format!(
                "{Q}query q() @cache(ttl = 60) {{ match {{ $p: Person }} return {{ $p.name }} }}"
            ),
            Works,
        ),
        // ---- Lance primitives with no frozen position
        (
            "Lance",
            "nulls placement in order",
            format!(
                "{Q}query q() {{ match {{ $p: Person }} return {{ $p.name }} order {{ $p.age desc nulls last }} }}"
            ),
            New,
        ),
        (
            "Lance",
            "offset",
            format!(
                "{Q}query q() {{ match {{ $p: Person }} return {{ $p.name }} order {{ $p.name }} limit 5 offset 10 }}"
            ),
            New,
        ),
        (
            "Lance",
            "vector search tuning options",
            format!(
                "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.vec: nearest($d.embedding, $v, nprobes: 20, refine: 2, ef: 64) }} return {{ $d.slug }} order {{ $d.vec }} limit 3 }}"
            ),
            Row,
        ),
        (
            "Lance",
            "full-text operator and boost options",
            format!(
                "{Q}query q($t: String) {{ match {{ $d: Doc search($d.text, $t, operator: \"and\", boost: 2.0) }} return {{ $d.slug }} }}"
            ),
            Row,
        ),
        (
            "Lance",
            "list membership index functions",
            format!(
                "{Q}query q() {{ match {{ $p: Person has_any($p.tags, [\"a\", \"b\"]) }} return {{ $p.name }} }}"
            ),
            Row,
        ),
        (
            "Lance",
            "multivector parameter",
            format!("{Q}query q($v: [Vector(3)]) {{ match {{ $d: Doc }} return {{ $d.slug }} }}"),
            New,
        ),
    ];
    let mut rows = vec!["owner\tcapability\texpected\tgot\tdetail".to_string()];
    let mut mismatches = Vec::new();
    for (owner, name, src, expected) in &cases {
        let (got, detail) = if src.starts_with("interface") {
            // A schema form: the one grammar's `file` rule.
            use pest::Parser;
            match super::Gq3Parser::parse(super::Rule::file, src) {
                Ok(_) => (Out::Works, "parses".to_string()),
                Err(e) => (Out::New, e.to_string()),
            }
        } else {
            classify(src)
        };
        rows.push(format!(
            "{owner}\t{name}\t{expected:?}\t{got:?}\t{}",
            detail.replace(['\n', '\t'], " ")
        ));
        if got != *expected {
            mismatches.push(format!(
                "{owner} / {name}: expected {expected:?}, got {got:?} ({detail})"
            ));
        }
    }
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/spike-gq3");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("later.tsv"), rows.join("\n")).unwrap();
    for (out, label) in [
        (Out::Slot, "slot"),
        (Out::Row, "row"),
        (Out::Works, "works"),
        (Out::New, "new production"),
        (Out::Misparse, "misparse"),
    ] {
        eprintln!("{label}: {}", cases.iter().filter(|c| c.3 == out).count());
    }
    assert!(mismatches.is_empty(), "{mismatches:#?}");
}

/// Forms that schema-level RFCs need, parsed by the one grammar's `file`.
#[test]
fn later_schema_forms_against_the_one_grammar() {
    use pest::Parser;
    let cases = [
        (
            "1202",
            "Vector with a distance",
            "node D { e: Vector(3, distance=\"cosine\") @index }",
            true,
        ),
        (
            "792",
            "@analyzed, keyword-first",
            "node D { t: String @analyzed(analyzer=\"standard_v1\", scorer=\"bm25_v1\") }",
            true,
        ),
        (
            "eval",
            "@valid with two positionals",
            "node A @valid(asserted_at, retracted_at) { asserted_at: DateTime retracted_at: DateTime? }",
            true,
        ),
        (
            "Lance",
            "index kind as a keyword argument",
            "node D { t: String @index(kind=\"ngram\") }",
            true,
        ),
        (
            "884",
            "interface with annotations",
            "interface Entity @description(\"d\") { name: String }",
            true,
        ),
        (
            "884",
            "interface implements interface",
            "interface A implements B { name: String }",
            false,
        ),
        (
            "884",
            "interface body constraint",
            "interface A { name: String @unique(name) }",
            true,
        ),
        (
            "884",
            "union edge endpoint",
            "edge HeldBy: Holding -> Person | Organization",
            false,
        ),
        (
            "1015",
            "namespaced type in implements and endpoints",
            "node Contact implements core::Named { name: String }\nedge WorksOn: Contact -> core::Project",
            true,
        ),
        (
            "Lance",
            "multivector property",
            "node D { e: [Vector(3)] }",
            false,
        ),
        (
            "Lance",
            "a new base type (Json)",
            "node D { j: Json }",
            false,
        ),
    ];
    let mut rows = Vec::new();
    for (owner, name, src, expected) in cases {
        let got = super::Gq3Parser::parse(super::Rule::file, &format!("gq 3;\n{src}")).is_ok();
        // The edition-2 schema grammar, for contrast.
        let today = crate::schema::parser::parse_schema(src).is_ok();
        rows.push(format!(
            "{owner}\t{name}\tone-grammar parses: {got}\tschema.pest parses: {today}"
        ));
        assert_eq!(got, expected, "{name}");
    }
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/spike-gq3");
    std::fs::write(dir.join("later-schema.tsv"), rows.join("\n")).unwrap();
}

/// Arithmetic is the one additive production that meets a frozen form: the
/// wildcard traversal `$a * $b` already owns `*` between two variables in
/// `match`, so a product of two variables there needs a lookahead.
#[test]
fn arithmetic_meets_the_wildcard_traversal() {
    let d = check(&format!("{Q}query q($x: I64, $y: I64) {{ match {{ $p: Person $x * $y > 10 }} return {{ $p.name }} }}")).unwrap_err();
    assert!(d.code.as_str().starts_with('Q'), "{d:?}");
}

/// Refusals PR #793 adds after edition 3 would break text edition 3 accepts.
#[test]
fn rfc_0048_refusals_against_edition_3() {
    let dup = check(&format!(
        "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.a: nearest($d.embedding, $v), $d.b: nearest($d.embedding, $v) }} return {{ $d.slug }} order {{ rrf($d.a, $d.b) }} limit 3 }}"
    ));
    let two_bindings = check(&format!(
        "{Q}query q($v: Vector(3), $t: String) {{ match {{ $d: Doc $p: Person }} retrieve {{ $d.a: nearest($d.embedding, $v), $p.b: bm25($p.bio, $t) }} return {{ $d.slug }} order {{ rrf($d.a, $p.b) }} limit 3 }}"
    ));
    let aggregate = check(&format!(
        "{Q}query q($v: Vector(3)) {{ match {{ $d: Doc }} retrieve {{ $d.a: nearest($d.embedding, $v) }} return {{ count($d) as n }} order {{ $d.a }} limit 3 }}"
    ));
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/spike-gq3");
    std::fs::create_dir_all(&dir).unwrap();
    let render = |r: &Result<_, crate::query::diagnostic::QueryDiagnostic>| match r {
        Ok(_) => "accepted".to_string(),
        Err(d) => format!("{}: {}", d.code, d.message),
    };
    std::fs::write(
        dir.join("rfc0048-refusals.txt"),
        format!(
            "duplicate arms: {}\narms on two bindings: {}\nnearest under an aggregate: {}\n",
            render(&dup),
            render(&two_bindings),
            render(&aggregate)
        ),
    )
    .unwrap();
    eprintln!(
        "duplicate arms: {}\narms on two bindings: {}\nnearest under an aggregate: {}",
        render(&dup),
        render(&two_bindings),
        render(&aggregate)
    );
}
