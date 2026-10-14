//! Version-pinned LCOV merge requests, separate from the native API.
use crate::common::Options;
use std::collections::BTreeSet;

pub mod cli;
pub mod run;

/// Immutable configuration for the opt-in LCOV 2.6 merge profile.
#[derive(Debug)]
pub struct Request {
    pub resources: Options,
    pub forget_test_names: bool,
    pub checksum: bool,
    pub mcdc_coverage: bool,
    pub ignore_errors: BTreeSet<String>,
    pub keep_going: bool,
    pub check_data_consistency: bool,
    pub derive_function_end_line: bool,
}

impl Default for Request {
    fn default() -> Self {
        let resources = Options {
            output: "-".into(),
            branch_coverage: false,
            ..Options::default()
        };
        Self {
            resources,
            forget_test_names: false,
            checksum: false,
            mcdc_coverage: false,
            ignore_errors: BTreeSet::new(),
            keep_going: false,
            check_data_consistency: true,
            derive_function_end_line: true,
        }
    }
}
