//! The instrument's data stays valid: every edition-3 ground truth is
//! accepted by #1316's front door, and where it runs it returns the task's
//! expected rows; every seed of each arm is refused with its code; and the
//! cards match their pins.

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
