//! The instrument's data stays valid: every edition-3 ground truth is
//! accepted by #1316's front door, and where it runs it returns the task's
//! expected rows; every seed of each arm is refused with its code; and each
//! card matches its pin and regenerates from the skill.

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
            24,
            "arm {} has one seed per refusal class this front end still refuses \
             (29 classes less the five it now accepts; seeds/gq3.seeds)",
            arm.name()
        );
    }
}

/// Every truth, and every disputed text, is accepted; a truth refused as a
/// form this release does not support yet (`T86`) is reported, not failed,
/// as a planner gap is: that is the front end's coverage, not the truth's
/// meaning.
#[test]
fn every_edition3_truth_is_accepted() {
    let instrument = instrument();
    let mut gaps = Vec::new();
    for task in &instrument.tasks {
        let catalog = catalog(&instrument.datasets[&task.dataset]).unwrap();
        for (index, truth) in task.edition3.iter().chain(&task.disputed).enumerate() {
            let verdict = accept(Arm::Gq3, &catalog, truth);
            if verdict.is_coverage_gap() {
                gaps.push(format!("{} truth {}: {}", task.id, index + 1, verdict.to_json()["diagnostic"]["message"]));
                continue;
            }
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
    eprintln!(
        "truths this release refuses as not supported yet (T86):\n{}",
        gaps.join("\n")
    );
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
fn cards_match_their_pins_and_regenerate_from_the_skill() {
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
    let skill = card::skill_root(&card::repo_root(&root));
    for (arm, _) in &manifest.cards {
        let generated = card::generated(*arm, &skill).unwrap();
        let on_disk = std::fs::read_to_string(card::card_path(&root, *arm)).unwrap();
        assert!(
            generated == on_disk,
            "skills/omnigraph changed, so the {} card no longer matches it; run \
             `omnigraph-gqt-agent cards --write`, which regenerates the card and its pin",
            arm.name()
        );
    }
}
