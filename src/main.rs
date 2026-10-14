fn main() {
    let result = lcovmerge::common::install_signal_handlers().and_then(|()| {
        let args: Vec<_> = std::env::args_os().collect();
        if lcovmerge::compat::cli::selected(&args) {
            return match lcovmerge::compat::cli::parse(args)? {
                Some(request) => lcovmerge::compat::run::run(request),
                None => Ok(()),
            };
        }
        match lcovmerge::cli::parse(args)? {
            Some(options) => lcovmerge::engine::run(options),
            None => Ok(()),
        }
    });
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(error.code);
    }
}
