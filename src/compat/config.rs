//! LCOV 2.6 configuration loading for the supported merge settings.
use crate::common::Error;
use std::{
    collections::HashSet,
    fs,
    path::{Path, PathBuf},
};

const MAX_INCLUDE_DEPTH: usize = 32;

#[derive(Debug, Default)]
pub struct Config {
    pub settings: Vec<(String, String)>,
}

impl Config {
    pub fn load(explicit: &[PathBuf]) -> Result<Self, Error> {
        Self::load_with(
            explicit,
            std::env::var_os("HOME").as_deref(),
            std::env::var_os("LCOV_HOME").as_deref(),
        )
    }

    fn load_with(
        explicit: &[PathBuf],
        home: Option<&std::ffi::OsStr>,
        lcov_home: Option<&std::ffi::OsStr>,
    ) -> Result<Self, Error> {
        let mut config = Self::default();
        let mut active = HashSet::new();
        if explicit.is_empty() {
            let candidates = [
                home.map(|home| PathBuf::from(home).join(".lcovrc")),
                lcov_home.map(|home| PathBuf::from(home).join("etc/lcovrc")),
            ];
            for candidate in candidates.into_iter().flatten() {
                if candidate.is_file() {
                    read_file(&candidate, 0, &mut active, &mut config.settings)?;
                    break;
                }
            }
        } else {
            for path in explicit {
                read_file(path, 0, &mut active, &mut config.settings)?;
            }
        }
        Ok(config)
    }
}

fn read_file(
    path: &Path,
    depth: usize,
    active: &mut HashSet<PathBuf>,
    settings: &mut Vec<(String, String)>,
) -> Result<(), Error> {
    if depth >= MAX_INCLUDE_DEPTH {
        return Err(Error::new(
            1,
            format!("configuration inclusion exceeds {MAX_INCLUDE_DEPTH} levels"),
        ));
    }
    let canonical = fs::canonicalize(path).map_err(|e| {
        Error::new(
            1,
            format!("cannot read configuration file '{}': {e}", path.display()),
        )
    })?;
    if !active.insert(canonical.clone()) {
        return Err(Error::new(
            1,
            format!(
                "configuration inclusion loop detected at '{}'",
                path.display()
            ),
        ));
    }
    let result = (|| {
        let content = fs::read_to_string(path).map_err(|e| {
            Error::new(
                1,
                format!("cannot read configuration file '{}': {e}", path.display()),
            )
        })?;
        for (line_number, line) in content.lines().enumerate() {
            let line = line.split('#').next().unwrap_or("").trim();
            if line.is_empty() {
                continue;
            }
            let Some((key, value)) = line.split_once('=') else {
                return Err(Error::new(
                    1,
                    format!(
                        "{}:{}: malformed configuration statement; expected key = value",
                        path.display(),
                        line_number + 1
                    ),
                ));
            };
            let key = key.trim();
            let value = expand_env(value.trim(), key, path, line_number + 1)?;
            if key == "config_file" {
                read_file(Path::new(&value), depth + 1, active, settings)?;
            } else if supported(key) {
                settings.push((key.to_owned(), value));
            } else if known_but_unsupported(key) {
                return Err(Error::new(
                    1,
                    format!("unsupported merge-relevant LCOV configuration setting: {key}"),
                ));
            }
            // LCOV ignores unknown keys while reading an rc file.
        }
        Ok(())
    })();
    active.remove(&canonical);
    result
}

fn expand_env(value: &str, key: &str, path: &Path, line: usize) -> Result<String, Error> {
    let mut out = String::new();
    let mut rest = value;
    while let Some(start) = rest.find("$ENV{") {
        out.push_str(&rest[..start]);
        let after = &rest[start + 5..];
        let Some(end) = after.find('}') else {
            out.push_str(&rest[start..]);
            return Ok(out);
        };
        let name = &after[..end];
        let env = std::env::var(name).map_err(|_| {
            Error::new(
                1,
                format!(
                    "{}:{line}: setting '{key}' uses unset environment variable '{name}'",
                    path.display()
                ),
            )
        })?;
        out.push_str(&env);
        rest = &after[end + 1..];
    }
    out.push_str(rest);
    Ok(out)
}

pub fn supported(key: &str) -> bool {
    matches!(
        key,
        "function_coverage"
            | "check_data_consistency"
            | "derive_function_end_line"
            | "branch_coverage"
            | "mcdc_coverage"
            | "checksum"
            | "forget_testcase_names"
            | "ignore_errors"
            | "include"
            | "exclude"
            | "parallel"
            | "lcov_tmp_dir"
    )
}

fn known_but_unsupported(key: &str) -> bool {
    matches!(
        key,
        "memory"
            | "memory_percentage"
            | "source_directory"
            | "build_directory"
            | "substitute"
            | "omit_lines"
            | "erase_functions"
            | "filter"
            | "resolve_script"
            | "context_script"
            | "version_script"
            | "compute_file_version"
            | "sort_input"
            | "case_insensitive"
            | "path_style"
            | "check_data_consistency"
            | "stop_on_error"
            | "no_exception_branch"
            | "derive_function_end_line"
            | "derive_function_end_line_all_files"
            | "trivial_function_threshold"
            | "lcov_json_module"
            | "lcov_excl_line"
            | "lcov_excl_br_line"
            | "lcov_excl_exception_br_line"
            | "lcov_excl_start"
            | "lcov_excl_stop"
            | "lcov_excl_br_start"
            | "lcov_excl_br_stop"
            | "lcov_excl_exception_br_start"
            | "lcov_excl_exception_br_stop"
            | "lcov_unreachable_start"
            | "lcov_unreachable_stop"
            | "lcov_unreachable_line"
            | "retain_unreachable_coverpoints_if_executed"
            | "ignore_unreachable_flag"
            | "max_message_count"
            | "message_log"
            | "expected_message_count"
            | "treat_warning_as_error"
            | "warn_once_per_file"
            | "rtl_file_extensions"
            | "c_file_extensions"
            | "perl_file_extensions"
            | "python_file_extensions"
            | "java_file_extensions"
            | "info_file_pattern"
            | "filter_lookahead"
            | "filter_bitwise_conditional"
            | "filter_blank_aggressive"
            | "profile"
            | "max_fork_fails"
            | "max_tasks_per_core"
            | "dedicate_segment_threshold"
            | "dedicate_segment_line_estimate"
            | "parallel_parse_min_lines"
            | "parallel_parse_chunks_per_worker"
            | "fork_fail_timeout"
            | "no_branch_coverage"
            | "criteria_script"
            | "unreachable_script"
            | "criteria_callback_data"
            | "criteria_callback_levels"
            | "demangle_cpp"
            | "excessive_count_threshold"
            | "split_char"
            | "cross_platform_read"
            | "check_existence_before_callback"
            | "fail_under_lines"
            | "fail_under_branches"
            | "lcov_filter_parallel"
            | "lcov_filter_chunk_size"
    ) && !supported(key)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn home_discovery_precedes_lcov_home_and_explicit_skips_discovery() {
        let root = tempfile::tempdir().unwrap();
        let home = root.path().join("home");
        let lcov_home = root.path().join("lcov");
        fs::create_dir_all(&home).unwrap();
        fs::create_dir_all(lcov_home.join("etc")).unwrap();
        fs::write(home.join(".lcovrc"), "branch_coverage = 0\n").unwrap();
        fs::write(lcov_home.join("etc/lcovrc"), "branch_coverage = 1\n").unwrap();

        let found =
            Config::load_with(&[], Some(home.as_os_str()), Some(lcov_home.as_os_str())).unwrap();
        assert_eq!(found.settings, vec![("branch_coverage".into(), "0".into())]);

        let explicit = root.path().join("explicit.rc");
        fs::write(&explicit, "branch_coverage = 1\n").unwrap();
        let found = Config::load_with(
            &[explicit],
            Some(home.as_os_str()),
            Some(lcov_home.as_os_str()),
        )
        .unwrap();
        assert_eq!(found.settings, vec![("branch_coverage".into(), "1".into())]);
    }

    #[test]
    fn allowlist_rejects_known_unimplemented_merge_settings() {
        assert!(supported("branch_coverage"));
        assert!(known_but_unsupported("memory"));
        assert!(known_but_unsupported("sort_input"));
    }
}
