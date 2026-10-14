use std::process::Command;

fn main() {
    for entry in ["HEAD", "refs", "packed-refs"] {
        if let Ok(output) = Command::new("git")
            .args(["rev-parse", "--git-path", entry])
            .output()
        {
            if output.status.success() {
                println!(
                    "cargo:rerun-if-changed={}",
                    String::from_utf8_lossy(&output.stdout).trim()
                );
            }
        }
    }
    println!("cargo:rerun-if-env-changed=LCOVMERGE_GIT_COMMIT");

    let commit = std::env::var("LCOVMERGE_GIT_COMMIT").ok().or_else(|| {
        Command::new("git")
            .args(["rev-parse", "--short=12", "HEAD"])
            .output()
            .ok()
            .filter(|output| output.status.success())
            .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_owned())
    });

    println!(
        "cargo:rustc-env=LCOVMERGE_GIT_COMMIT={}",
        commit
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "unknown".to_owned())
    );
}
