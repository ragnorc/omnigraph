//! Settling a publication whose answer was lost, from main's history.
//!
//! A run that publishes under an exact graph-head expectation can only land
//! as the commit whose parent is the head every attempt read. Once main's
//! graph head moves, the input head's child is that commit or proves the run
//! did not commit: every `__manifest` version an attempt could have taken is
//! occupied, and a later attempt reads the moved head and refuses.

use super::*;
use crate::db::graph_coordinator::GraphCoordinator;

/// What main's history says about a publication with a given intent nonce.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum PublicationLookup {
    /// main's graph head is still the input head. A configuration-only
    /// `__manifest` version does not move it, and an attempt at the version
    /// after one can still be in flight.
    HeadUnchanged,
    /// The input head's child answers the nonce: the run committed as it.
    Committed { commit_id: String },
    /// The input head's child does not answer the nonce.
    NotCommitted,
    /// The walk could not reach the input head's child, so nothing is proven
    /// either way; `reason` says where it stopped.
    Unproven { reason: String },
    /// SPIKE: the walk read `resolution_walk_limit` commits without meeting
    /// the input head's child. `next` is the first parent the page did not
    /// visit (`FirstParentPage::next`); the next attempt starts there.
    Walking { next: String, covered: u64 },
}

impl Omnigraph {
    /// SPIKE: one bounded resolution attempt. The graph-head test runs
    /// first on a fresh read of main; then one `first_parent_page` of at
    /// most `limit` commits, from `resume` (the `next` of the previous
    /// attempt) or from the head. A page never skips a commit: `next` is the
    /// parent it did not visit.
    pub async fn resolve_publication_page(
        &self,
        nonce: &str,
        input_commit: Option<&str>,
        input_manifest_version: u64,
        resume: Option<&str>,
        covered: u64,
        limit: usize,
    ) -> Result<PublicationLookup> {
        let coordinator = self.open_coordinator_for_branch(None).await?;
        let head_id = coordinator.head_commit().graph_commit_id;
        let (head, page) = coordinator
            .first_parent_page(Some(resume.unwrap_or(&head_id)), limit.max(1))
            .await?;
        if Some(head.graph_commit_id.as_str()) == input_commit {
            return Ok(PublicationLookup::HeadUnchanged);
        }
        let Some(page) = page else {
            return Ok(PublicationLookup::Unproven {
                reason: format!(
                    "no commit {} is held or in __history",
                    resume.unwrap_or(&head.graph_commit_id)
                ),
            });
        };
        let read = page.commits.len() as u64;
        for commit in page.commits {
            if commit.parent_commit_id.as_deref() == input_commit {
                return Ok(
                    match omnigraph_core::graph_commit_id::commit_id_answers(
                        &commit.graph_commit_id,
                        nonce,
                    ) {
                        Ok(true) => PublicationLookup::Committed {
                            commit_id: commit.graph_commit_id,
                        },
                        Ok(false) => PublicationLookup::NotCommitted,
                        Err(error) => PublicationLookup::Unproven {
                            reason: format!(
                                "the input head's child {} has no readable id: {error}",
                                commit.graph_commit_id
                            ),
                        },
                    },
                );
            }
            if commit.graph_manifest_version <= input_manifest_version {
                return Ok(PublicationLookup::Unproven {
                    reason: format!(
                        "the walk passed the input head's version at {}",
                        commit.graph_commit_id
                    ),
                });
            }
        }
        Ok(match page.next {
            Some(next) => PublicationLookup::Walking {
                next,
                covered: covered + read,
            },
            None => PublicationLookup::Unproven {
                reason: "the walk reached the first commit without meeting the input head"
                    .to_string(),
            },
        })
    }

    /// Walk main's `parent_commit_id` chain from the head of a fresh read of
    /// its `__manifest` back to the child of `input_commit`, and judge that
    /// child against `nonce`. Each step looks its parent up where a
    /// commit-addressed read does: the head row and the commit buffer, then
    /// `__history`, then the live branches. Every commit on main is written
    /// on main, so each step's `graph_manifest_version` is below the one
    /// before it, and the walk stops at the first link not above
    /// `input_manifest_version`.
    pub async fn resolve_publication(
        &self,
        nonce: &str,
        input_commit: Option<&str>,
        input_manifest_version: u64,
    ) -> Result<PublicationLookup> {
        let coordinator = self.open_coordinator_for_branch(None).await?;
        walk_to_child(&coordinator, nonce, input_commit, input_manifest_version).await
    }
}

async fn walk_to_child(
    coordinator: &GraphCoordinator,
    nonce: &str,
    input_commit: Option<&str>,
    input_manifest_version: u64,
) -> Result<PublicationLookup> {
    let mut current = coordinator.head_commit();
    if Some(current.graph_commit_id.as_str()) == input_commit {
        return Ok(PublicationLookup::HeadUnchanged);
    }
    loop {
        if current.parent_commit_id.as_deref() == input_commit {
            return Ok(
                match omnigraph_core::graph_commit_id::commit_id_answers(
                    &current.graph_commit_id,
                    nonce,
                ) {
                    Ok(true) => PublicationLookup::Committed {
                        commit_id: current.graph_commit_id,
                    },
                    Ok(false) => PublicationLookup::NotCommitted,
                    Err(error) => PublicationLookup::Unproven {
                        reason: format!(
                            "the input head's child {} has no readable id: {error}",
                            current.graph_commit_id
                        ),
                    },
                },
            );
        }
        if current.graph_manifest_version <= input_manifest_version {
            return Ok(PublicationLookup::Unproven {
                reason: format!(
                    "the walk passed the input head's version at {}",
                    current.graph_commit_id
                ),
            });
        }
        let Some(parent) = current.parent_commit_id.clone() else {
            return Ok(PublicationLookup::Unproven {
                reason: "the walk reached the first commit without meeting the input head"
                    .to_string(),
            });
        };
        current = match coordinator.published_record(&parent).await {
            Ok(Some(record)) => {
                crate::db::commit_graph::graph_commit_from_manifest_row(record.commit)
            }
            Ok(None) => {
                return Ok(PublicationLookup::Unproven {
                    reason: format!("no record of commit {parent}"),
                });
            }
            Err(error) => {
                return Ok(PublicationLookup::Unproven {
                    reason: format!("reading commit {parent}: {error}"),
                });
            }
        };
    }
}
