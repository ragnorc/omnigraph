//! The reference cards an agent reads, one per arm, and their pins.
//!
//! Arm `gq3`'s card is generated from the bundled agent skill
//! (`skills/omnigraph`) at the commit being measured, whole sections by
//! heading, by the code and the section selection that generate the
//! edition-2 card from `main`'s skill (fork branch `gq3/phase-0-instrument`):
//! both arms measure the skill agents are given. The card is pinned by
//! SHA-256 in `manifest.json`, which the runner checks before every run, and
//! the instrument's test regenerates it, so the skill cannot drift from the
//! card unnoticed.

use std::path::{Path, PathBuf};

use super::Arm;

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

/// The skill sections a card holds, in order: the file under
/// `skills/omnigraph` and the exact heading line. A missing heading fails the
/// generation instead of shrinking the card. The edition-2 card's selection,
/// unchanged.
const SECTIONS: &[(&str, &str)] = &[
    ("SKILL.md", "### `.gq` grammar"),
    ("references/queries.md", "## Query Structure"),
    (
        "references/queries.md",
        "## System Fields and Result Values",
    ),
    ("references/queries.md", "## Search Functions"),
    ("references/queries.md", "## Aggregations"),
    ("references/queries.md", "## Filter Operators"),
    ("references/queries.md", "## Mutations"),
];

/// The edition-2 card's preface with the arm's name in place of
/// `edition 2`.
const GQ3_PREFACE: &str = "# GQ reference card (GQ 3)\n\n\
The query language as it ships today: the grammar notes of the Omnigraph agent skill and its \
query reference, section by section.\n";

pub const MANIFEST: &str = "manifest.json";

fn heading_level(line: &str) -> Option<usize> {
    let level = line.bytes().take_while(|b| *b == b'#').count();
    (level > 0 && line.as_bytes().get(level) == Some(&b' ')).then_some(level)
}

/// The section of `text` that starts at the line `heading` and runs to the
/// next heading of the same or a higher level outside a code fence.
fn section<'a>(text: &'a str, heading: &str) -> Option<&'a str> {
    let level = heading_level(heading)?;
    let mut start = None;
    let mut offset = 0;
    let mut fenced = false;
    for line in text.split_inclusive('\n') {
        let bare = line.trim_end_matches(['\n', '\r']);
        if bare.trim_start().starts_with("```") {
            fenced = !fenced;
        } else if !fenced {
            match (start, heading_level(bare)) {
                (None, _) if bare == heading => start = Some(offset),
                (Some(begin), Some(found)) if found <= level => {
                    return Some(&text[begin..offset]);
                }
                _ => {}
            }
        }
        offset += line.len();
    }
    start.map(|begin| &text[begin..])
}

/// A card generated from the skill under `skill_root`: the preface, then
/// each selected section.
fn generate(skill_root: &Path, preface: &str) -> Result<String, String> {
    let mut out = String::from(preface);
    for (file, heading) in SECTIONS {
        let path = skill_root.join(file);
        let text =
            std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let body = section(&text, heading)
            .ok_or_else(|| format!("{}: no heading `{heading}`", path.display()))?;
        out.push('\n');
        out.push_str(body.trim_end());
        out.push('\n');
    }
    Ok(out)
}

/// The card of `arm`, generated from the skill under `skill_root`.
pub fn generated(arm: Arm, skill_root: &Path) -> Result<String, String> {
    match arm {
        Arm::Gq3 => generate(skill_root, GQ3_PREFACE),
    }
}

/// The skill this checkout ships: `<repo>/skills/omnigraph`.
pub fn skill_root(repo_root: &Path) -> PathBuf {
    repo_root.join("skills").join("omnigraph")
}

/// The repository root of an instrument directory (`crates/omnigraph-gqt/agent`).
pub fn repo_root(instrument_root: &Path) -> PathBuf {
    instrument_root.join("..").join("..").join("..")
}

/// The card an arm's agent reads: `cards/<arm>.md`.
pub fn card_path(root: &Path, arm: Arm) -> PathBuf {
    root.join("cards").join(format!("{}.md", arm.name()))
}

pub fn sha256(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// `manifest.json`: the pinned agent model and each card's digest.
#[derive(Debug, Clone, PartialEq)]
pub struct Manifest {
    pub model: String,
    pub cards: Vec<(Arm, String)>,
}

impl Manifest {
    pub fn read(root: &Path) -> Result<Self, String> {
        let path = root.join(MANIFEST);
        let text =
            std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let value: Value =
            serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
        let model = value["model"]
            .as_str()
            .ok_or("manifest: `model` must be a string")?
            .to_string();
        let mut cards = Vec::new();
        for arm in Arm::ALL {
            let digest = value["cards"][format!("{}.md", arm.name())]
                .as_str()
                .ok_or_else(|| format!("manifest: no digest for {}.md", arm.name()))?;
            cards.push((arm, digest.to_string()));
        }
        Ok(Manifest { model, cards })
    }

    pub fn render(&self) -> String {
        let cards: serde_json::Map<String, Value> = self
            .cards
            .iter()
            .map(|(arm, digest)| (format!("{}.md", arm.name()), json!(digest)))
            .collect();
        let mut out = serde_json::to_string_pretty(&json!({
            "model": self.model,
            "cards": cards,
        }))
        .expect("manifest serializes");
        out.push('\n');
        out
    }

    /// Every card on disk against its pinned digest; the mismatches.
    pub fn verify(&self, root: &Path) -> Vec<String> {
        let mut out = Vec::new();
        for (arm, pinned) in &self.cards {
            let path = card_path(root, *arm);
            match std::fs::read(&path) {
                Ok(bytes) if sha256(&bytes) == *pinned => {}
                Ok(bytes) => out.push(format!(
                    "{} has digest {} but manifest.json pins {pinned}",
                    path.display(),
                    sha256(&bytes)
                )),
                Err(e) => out.push(format!("{}: {e}", path.display())),
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_section_ends_at_the_next_heading_of_its_level_outside_fences() {
        let text = "# A\n## B\nb\n```\n## not a heading\n```\n### C\nc\n## D\nd\n";
        assert_eq!(
            section(text, "## B"),
            Some("## B\nb\n```\n## not a heading\n```\n### C\nc\n")
        );
        assert_eq!(section(text, "## D"), Some("## D\nd\n"));
        assert_eq!(section(text, "## E"), None);
    }
}
