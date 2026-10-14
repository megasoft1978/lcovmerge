fn main() {
    let args: Vec<_> = std::env::args_os().collect();
    let compat = lcovmerge::compat::cli::selected(&args);
    let result = lcovmerge::common::install_signal_handlers().and_then(|()| {
        if compat {
            match lcovmerge::compat::cli::parse(args)? {
                Some(request) => lcovmerge::compat::run::run(request),
                None => Ok(()),
            }
        } else {
            match lcovmerge::cli::parse(args)? {
                Some(options) => lcovmerge::engine::run(options),
                None => Ok(()),
            }
        }
    });
    if let Err(error) = result {
        eprintln!("{error}");
        // LCOV reports merge failures with status 1. Native lcovmerge keeps
        // its existing usage/input/I/O status distinctions.
        std::process::exit(if compat { 1 } else { error.code });
    }
}
