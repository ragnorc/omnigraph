//! The instrument's data stays valid: every edition-3 ground truth is
//! accepted by #1316's front door, and where it runs it returns the task's
//! expected rows; every seed of each arm is refused with its code; the cards
//! match their pins; and the variant arm lowers every ground truth,
//! converted mechanically, to exactly the core IR of the GQ 3 text.

use super::*;

fn instrument() -> Instrument {
    Instrument::load(&Instrument::default_root()).expect("the agent instrument loads")
}

#[test]
fn the_instrument_has_the_shape_stage_m0_names() {
    let instrument = instrument();
    let set = |name: &'static str| instrument.tasks.iter().filter(move |t| t.set == name);
    assert_eq!(
        set("tasks").count(),
        45,
        "the competence instrument holds 45 tasks"
    );
    assert_eq!(set("corpus").count(), 30, "the agent corpus holds 30 tasks");
    for task in &instrument.tasks {
        task.signature().unwrap();
    }
    for arm in Arm::ALL {
        assert_eq!(
            instrument
                .seeds
                .iter()
                .filter(|s| s.arm == arm.name())
                .count(),
            29,
            "arm {} has one seed per GQ 3 seed",
            arm.name()
        );
    }
}

#[test]
fn every_edition3_truth_is_accepted() {
    let instrument = instrument();
    for task in &instrument.tasks {
        let catalog = catalog(&instrument.datasets[&task.dataset]).unwrap();
        for (index, truth) in task.edition3.iter().enumerate() {
            let verdict = accept(Arm::Gq3, &catalog, truth);
            assert!(
                verdict.is_accepted(),
                "{}: edition-3 truth {} is refused: {}",
                task.id,
                index + 1,
                verdict.to_json()
            );
        }
        if let Some(check) = &task.check {
            assert!(
                accept(Arm::Gq3, &catalog, check).is_accepted(),
                "{}: the check",
                task.id
            );
        }
    }
}

/// Every truth that runs returns the expected rows; a truth the planner
/// does not lower (`P006`) is reported, not failed, since that is the
/// engine's coverage, not the truth's meaning.
#[tokio::test]
async fn edition3_truths_that_run_return_the_expected_rows() {
    let instrument = instrument();
    let mut ran = 0;
    let mut gaps = Vec::new();
    for task in &instrument.tasks {
        let Ok(expected) = expected_rows(&instrument, task).await else {
            continue;
        };
        for (index, truth) in task.edition3.iter().enumerate() {
            match run_truth(&instrument, task, truth).await {
                Ok(rows) => {
                    assert!(
                        rows.same_values(&expected, task.ordered()),
                        "{}: edition-3 truth {} returns other rows than the expected ones\n\
                         expected: {expected:?}\nnow: {rows:?}",
                        task.id,
                        index + 1
                    );
                    ran += 1;
                }
                Err(e) => gaps.push(format!("{} truth {}: {e}", task.id, index + 1)),
            }
        }
    }
    eprintln!(
        "truths that do not run on this engine:\n{}",
        gaps.join("\n")
    );
    assert!(ran >= 40, "only {ran} truths ran");
}

#[test]
fn every_seed_is_refused_with_its_code() {
    let instrument = instrument();
    for seed in &instrument.seeds {
        let arm = Arm::parse(&seed.arm).unwrap();
        let task = instrument.task(&seed.task).unwrap();
        let catalog = catalog(&instrument.datasets[&task.dataset]).unwrap();
        let verdict = accept(arm, &catalog, &seed.text);
        assert!(
            matches!(verdict, Verdict::Refused(_)) && verdict.code() == seed.code.as_deref(),
            "seed {} ({}) should be refused with {:?}: {}",
            seed.id,
            seed.class,
            seed.code,
            verdict.to_json()
        );
    }
}

#[test]
fn cards_match_their_pins() {
    let root = Instrument::default_root();
    let manifest = card::Manifest::read(&root).unwrap();
    assert!(
        !manifest.model.is_empty(),
        "manifest.json pins the agent model"
    );
    let mismatches = manifest.verify(&root);
    assert!(
        mismatches.is_empty(),
        "a card changed without its pin; review the card, then run \
         `omnigraph-gqt-agent cards --write`:\n{}",
        mismatches.join("\n")
    );
}

/// The variant's lowering: every edition-3 ground truth, converted
/// mechanically, parses in the variant and checks to exactly its core IR.
#[test]
fn every_truth_converted_to_the_variant_lowers_to_the_same_core_ir() {
    let instrument = instrument();
    let mut checked = 0;
    for task in &instrument.tasks {
        let catalog = catalog(&instrument.datasets[&task.dataset]).unwrap();
        for (index, truth) in task.edition3.iter().enumerate() {
            let report = variant::lowering_report(&catalog, truth);
            assert_eq!(
                report["status"],
                "equal",
                "{} truth {}: {report:#}",
                task.id,
                index + 1
            );
            checked += 1;
        }
    }
    assert!(checked >= 90, "only {checked} truths were converted");
}

fn code_blocks(card: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut block: Option<String> = None;
    for line in card.lines() {
        if line.starts_with("```") {
            match block.take() {
                Some(b) if b.trim_start().starts_with("query ") && !b.contains("<") => out.push(b),
                Some(_) => {}
                None => block = Some(String::new()),
            }
            continue;
        }
        if let Some(b) = &mut block {
            b.push_str(line);
            b.push('\n');
        }
    }
    out
}

/// The two cards' examples are the same queries: each pair lowers to equal
/// core IR.
#[test]
fn the_cards_examples_are_the_same_queries_in_each_arm() {
    let root = Instrument::default_root();
    let instrument = instrument();
    let catalog = catalog(&instrument.datasets["section8"]).unwrap();
    let read = |arm: Arm| std::fs::read_to_string(card::card_path(&root, arm)).unwrap();
    let gq3 = code_blocks(&read(Arm::Gq3));
    let variant = code_blocks(&read(Arm::Where));
    assert_eq!(gq3.len(), variant.len());
    assert!(gq3.len() >= 4);
    for (a, b) in gq3.iter().zip(&variant) {
        let (Verdict::Accepted { decls: x, .. }, Verdict::Accepted { decls: y, .. }) = (
            accept(Arm::Gq3, &catalog, a),
            accept(Arm::Where, &catalog, b),
        ) else {
            panic!("a card example is refused:\n{a}\n{b}");
        };
        let raw = |d: &[Declaration]| d.iter().map(|d| d.raw_ir.clone()).collect::<Vec<_>>();
        assert_eq!(raw(&x), raw(&y), "the examples differ:\n{a}\n{b}");
    }
}

/// The refusals the variant adds, with their codes, and fixes in variant
/// syntax: applying the suggestion yields text the variant accepts.
#[test]
fn the_variant_refuses_gq3_forms_with_fixes_in_variant_syntax() {
    let instrument = instrument();
    let catalog = catalog(&instrument.datasets["section8"]).unwrap();
    let cases: &[(&str, &str)] = &[
        // A filter written as an element.
        (
            "query q() { match { $p: Person, $p.age > 30 } return { $p.name } }",
            "Q020",
        ),
        // Elements separated by whitespace.
        (
            "query q() { match { $p: Person  $p worksAt $c } return { $p.name } }",
            "Q001",
        ),
        (
            "query q() { match {\n $p: Person\n $p worksAt $c\n} return { $p.name } }",
            "Q001",
        ),
        // A binding's braces.
        (
            "query q($h: String) { match { $p: Person { handle: $h } } return { $p.name } }",
            "Q010",
        ),
        (
            "query q() { match { $d: Doc { year >= 2025 } } return { $d.title } }",
            "Q010",
        ),
        // `not { … }` as an element.
        (
            "query q() { match { $p: Person, not { $p worksAt $c } } return { $p.name } }",
            "Q020",
        ),
        // `where` inside the braces.
        (
            "query q() { match { $p: Person where $p.age > 3 } return { $p.name } }",
            "Q020",
        ),
        // A write group's clauses written bare.
        (
            "query q($h: String) { $p: Person  $p.handle = $h  delete $p }",
            "Q005",
        ),
        // A GQ 3 read whole.
        (
            "query q($h: String) { match { $p: Person { handle: $h }  $p worksAt $c  $c.name != \"x\"  not { $p knows $f } } return { $c.name } }",
            "Q010",
        ),
        // A clause after a statement.
        (
            "query q($h: String) { match { $p: Person } where $p.handle = $h  delete $p  $p.age > 3 }",
            "Q013",
        ),
        // `update T … where`.
        (
            "query q($h: String) { update Person set { age: age + 1 } where handle = $h }",
            "Q008",
        ),
        // A Cypher pattern, fixed in two steps (triples, then braces to `where`).
        (
            "query q() { match { (a:Person {name: \"Ann\"})-[:KNOWS]->(b) } return { $b.name } }",
            "Q014",
        ),
    ];
    for (text, code) in cases {
        let verdict = accept(Arm::Where, &catalog, text);
        assert_eq!(verdict.code(), Some(*code), "{text}: {}", verdict.to_json());
        let Verdict::Refused(d) = &verdict else {
            unreachable!()
        };
        let edits: Vec<(usize, usize, String)> = d["suggestion"]["edits"]
            .as_array()
            .map(|a| {
                a.iter()
                    .map(|e| {
                        (
                            e["start"].as_u64().unwrap() as usize,
                            e["end"].as_u64().unwrap() as usize,
                            e["replacement"].as_str().unwrap().to_string(),
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        assert!(!edits.is_empty(), "{text}: no edits: {}", verdict.to_json());
        let mut fixed = text.to_string();
        let mut sorted = edits.clone();
        sorted.sort_by_key(|e| std::cmp::Reverse(e.0));
        for (start, end, replacement) in sorted {
            fixed.replace_range(start..end, &replacement);
        }
        assert!(
            variant::parse_source(&fixed).is_ok(),
            "{text}: the fix is not variant text:\n{fixed}\n{}",
            verdict.to_json()
        );
    }
    // Accepted forms: a trailing comma, a block's own `where`, `not exists`
    // under `or`, an `optional` with its `where`, a plain insert.
    for text in [
        "query q() { match { $p: Person, $p worksAt $c, } return { $p.name } }",
        "query q() { match { $p: Person } where count { $p knows $f where $f.age > 3 } >= 1 return { $p.name } }",
        "query q() { match { $p: Person } where $p.age > 60 or not exists { $p worksAt $c } return { $p.name } }",
        "query q() { match { $p: Person, optional { $p worksAt $c where $c.name != \"x\" } } return { $p.name, $c.name } }",
        "query q($h: String) { insert Person { handle: $h, name: $h } }",
        "query q($h: String) { match { $p: Person } where $p.handle = $h  update $p set { city: null } }",
    ] {
        let verdict = accept(Arm::Where, &catalog, text);
        assert!(verdict.is_accepted(), "{text}: {}", verdict.to_json());
    }
}
