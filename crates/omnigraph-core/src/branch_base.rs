//! Native `__manifest` branch bases follow the opened graph root (issue 1233).
//!
//! Lance 13 `Dataset::create_branch` is a shallow clone. It records the
//! source branch's absolute URI in the new branch manifest's `base_paths`
//! (`Operation::Clone.ref_path`, then `Manifest::shallow_clone`) and sets
//! `base_id` on every inherited data, deletion and index file. Lance resolves
//! a `base_id` file only through `Manifest::base_paths`
//! (`Dataset::data_file_dir_for_base`, `dataset_dir_for_deletion`,
//! `indice_files_dir`, `object_store(Some(id))`), so a graph root copied to a
//! new path still reads a retained branch's inherited files at the path the
//! branch was created at, or fails when that path is gone.
//!
//! The source of a `__manifest` branch is always a branch of the same
//! `__manifest` dataset: graph branch creation clones only `__manifest`,
//! from a branch of that dataset, and nothing else adds a `__manifest` base.
//! Its location is therefore fixed by the opened location
//! (`BranchLocation::find_branch(name)` of the base's recorded source branch
//! name), and the recorded URI is a second, frozen copy of that fact.
//! [`follow_opened_root`] replaces such a base in the handle's in-memory
//! manifest with the opened location; the persisted bytes are unchanged.
//!
//! The rule is fenced: it applies only to a dataset opened at a `__manifest`
//! directory, only to a dataset-root base, and only when the recorded URI ends
//! with exactly the dataset-relative suffix the base's own branch name selects
//! (`/__manifest` for main, `/__manifest/tree/<name>` for a branch). Any other
//! base keeps its recorded URI. A rebased file that is missing in the opened
//! root fails there; nothing falls back to the recorded path.

use std::sync::Arc;

use lance::Dataset;
use lance_core::Result;

const MANIFEST_DIR: &str = "__manifest";
const BRANCH_DIR: &str = "tree";

/// Rebind every same-dataset branch base of an opened `__manifest` handle to
/// the opened root. A handle with no such base, or a dataset not opened at a
/// `__manifest` directory, is returned unchanged.
pub fn follow_opened_root(mut dataset: Dataset) -> Result<Dataset> {
    rebind_internal_bases(&mut dataset)?;
    Ok(dataset)
}

/// [`follow_opened_root`] in place.
pub fn rebind_internal_bases(dataset: &mut Dataset) -> Result<()> {
    if dataset.manifest.base_paths.is_empty() {
        return Ok(());
    }
    let location = dataset.branch_location();
    let main = location.find_main()?;
    if !path_part(&main.uri)
        .trim_end_matches('/')
        .ends_with(&format!("/{MANIFEST_DIR}"))
    {
        return Ok(());
    }
    let mut rebased = None;
    for (id, base) in dataset.manifest.base_paths.iter() {
        if !base.is_dataset_root {
            continue;
        }
        let suffix = match base.name.as_deref() {
            None => format!("/{MANIFEST_DIR}"),
            Some(name) => format!("/{MANIFEST_DIR}/{BRANCH_DIR}/{name}"),
        };
        if !path_part(&base.path)
            .trim_end_matches('/')
            .ends_with(&suffix)
        {
            continue;
        }
        let opened = if base.name.is_none() {
            main.uri.clone()
        } else {
            main.find_branch(base.name.as_deref())?.uri
        };
        if opened == base.path {
            continue;
        }
        let mut rebound = base.clone();
        rebound.path = opened;
        rebased
            .get_or_insert_with(|| dataset.manifest.base_paths.clone())
            .insert(*id, rebound);
    }
    if let Some(base_paths) = rebased {
        let mut manifest = dataset.manifest.as_ref().clone();
        manifest.base_paths = base_paths;
        dataset.manifest = Arc::new(manifest);
    }
    Ok(())
}

fn path_part(uri: &str) -> &str {
    uri.split_once('?').map_or(uri, |(path, _)| path)
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use lance_table::format::BasePath;

    use super::*;
    use crate::lance_clone::fresh_dataset;

    /// A real dataset at `<root>/<dir>` whose manifest carries `bases`.
    async fn with_bases(root: &std::path::Path, dir: &str, bases: Vec<BasePath>) -> Dataset {
        let uri = root.join(dir);
        let mut dataset = fresh_dataset(uri.to_str().unwrap()).await;
        let mut manifest = dataset.manifest.as_ref().clone();
        manifest.base_paths = bases.into_iter().map(|base| (base.id, base)).collect();
        dataset.manifest = Arc::new(manifest);
        dataset
    }

    fn paths(dataset: &Dataset) -> HashMap<u32, String> {
        dataset
            .manifest
            .base_paths
            .iter()
            .map(|(id, base)| (*id, base.path.clone()))
            .collect()
    }

    #[tokio::test]
    async fn only_same_dataset_branch_bases_follow_the_opened_root() {
        let dir = tempfile::tempdir().unwrap();
        let opened = dir.path().join("copy.omni");
        let opened_manifest = format!("{}/__manifest", opened.to_str().unwrap());
        let dataset = with_bases(
            &opened,
            "__manifest",
            vec![
                // Main of this `__manifest`, recorded at the old root.
                BasePath::new(1, "/old/root/__manifest".into(), None, true),
                // A parent branch tree, recorded at the old root.
                BasePath::new(
                    2,
                    "/old/root/__manifest/tree/parent.01J".into(),
                    Some("parent.01J".into()),
                    true,
                ),
                // A branch name that does not match the recorded tree.
                BasePath::new(
                    3,
                    "/old/root/__manifest/tree/other".into(),
                    Some("parent.01J".into()),
                    true,
                ),
                // A truly external dataset and a data-only base.
                BasePath::new(4, "s3://bucket/elsewhere/table.lance".into(), None, true),
                BasePath::new(5, "/old/root/__manifest".into(), None, false),
            ],
        )
        .await;
        let rebound = follow_opened_root(dataset).unwrap();
        let paths = paths(&rebound);
        assert_eq!(paths[&1], opened_manifest);
        assert_eq!(paths[&2], format!("{opened_manifest}/tree/parent.01J"));
        assert_eq!(paths[&3], "/old/root/__manifest/tree/other");
        assert_eq!(paths[&4], "s3://bucket/elsewhere/table.lance");
        assert_eq!(paths[&5], "/old/root/__manifest");

        // Idempotent: a rebound handle is returned as it is.
        let again = follow_opened_root(rebound.clone()).unwrap();
        assert!(Arc::ptr_eq(&again.manifest, &rebound.manifest));
    }

    #[tokio::test]
    async fn a_dataset_outside_a_manifest_directory_keeps_every_base() {
        let dir = tempfile::tempdir().unwrap();
        let dataset = with_bases(
            dir.path(),
            "node_Person.lance",
            vec![BasePath::new(1, "/old/root/__manifest".into(), None, true)],
        )
        .await;
        let manifest = Arc::clone(&dataset.manifest);
        let unchanged = follow_opened_root(dataset).unwrap();
        assert!(Arc::ptr_eq(&unchanged.manifest, &manifest));
    }
}
