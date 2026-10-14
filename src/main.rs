fn main() {
    let result = lcovmerge::common::install_signal_handlers().and_then(|()| {
        match lcovmerge::cli::parse(std::env::args_os())? {
            Some(options) => lcovmerge::engine::run(options),
            None => Ok(()),
        }
    });
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(error.code);
    }
}
