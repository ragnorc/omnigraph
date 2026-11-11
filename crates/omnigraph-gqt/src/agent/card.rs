//! The reference cards an agent reads, one per arm, and their pins.
//!
//! Arm `gq3`'s card is the edition-3 card of the 2026-11-09 run, changed
//! only where #1316 differs from it; arm `where`'s card is that card with
//! only the syntax changed. Both are written by hand and pinned by SHA-256 in
//! `manifest.json`, which the runner checks before every run.

use std::path::{Path, PathBuf};

use super::Arm;

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

pub const MANIFEST: &str = "manifest.json";

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
