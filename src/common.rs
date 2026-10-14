use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};

pub const MAX_LINE: usize = 1 << 20;
pub const IO_BLOCK: usize = 1 << 14;
pub const MIN_WORKER_BUDGET: usize = 8 << 20;
static CANCELLED: AtomicBool = AtomicBool::new(false);

#[derive(Clone, Debug)]
pub struct Options {
    pub inputs: Vec<PathBuf>,
    pub includes: Vec<String>,
    pub excludes: Vec<String>,
    pub rebases: Vec<(String, String)>,
    pub prefix_strip: Option<String>,
    pub output: PathBuf,
    pub tmpdir: PathBuf,
    pub mem_limit: usize,
    pub jobs: usize,
    pub jobs_explicit: bool,
    pub branch_coverage: bool,
    pub function_data: bool,
    pub quiet: bool,
    pub verbose: bool,
    pub stats: bool,
    pub warn_unknown: bool,
    pub strict_checksum: bool,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            inputs: Vec::new(),
            includes: Vec::new(),
            excludes: Vec::new(),
            rebases: Vec::new(),
            prefix_strip: None,
            output: PathBuf::new(),
            tmpdir: std::env::temp_dir(),
            mem_limit: 64 << 20,
            jobs: std::thread::available_parallelism().map_or(1, |n| n.get().min(4)),
            jobs_explicit: false,
            branch_coverage: true,
            function_data: true,
            quiet: false,
            verbose: false,
            stats: false,
            warn_unknown: false,
            strict_checksum: false,
        }
    }
}

#[derive(Debug)]
pub struct Error {
    pub code: i32,
    pub message: String,
}

impl Error {
    pub fn new(code: i32, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
    pub fn at(
        code: i32,
        filename: impl AsRef<std::path::Path>,
        line: u64,
        message: impl Into<String>,
    ) -> Self {
        let message = message.into();
        Self::new(
            code,
            format!("{}:{line}: {message}", filename.as_ref().display()),
        )
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "lcovmerge: {}", self.message)
    }
}
impl std::error::Error for Error {}

pub fn cancelled() -> bool {
    CANCELLED.load(Ordering::Relaxed)
}
pub fn check_cancelled() -> Result<(), Error> {
    if cancelled() {
        Err(Error::new(3, "interrupted"))
    } else {
        Ok(())
    }
}

pub fn stdin_reader() -> std::io::Result<Box<dyn std::io::Read + Send>> {
    #[cfg(unix)]
    {
        use std::os::fd::AsFd;
        let handle = std::io::stdin().as_fd().try_clone_to_owned()?;
        Ok(Box::new(CancellableInput(std::fs::File::from(handle))))
    }
    #[cfg(not(unix))]
    {
        Ok(Box::new(std::io::stdin()))
    }
}

pub fn stdout_writer() -> std::io::Result<Box<dyn std::io::Write>> {
    #[cfg(unix)]
    {
        use std::os::fd::AsFd;
        let handle = std::io::stdout().as_fd().try_clone_to_owned()?;
        Ok(Box::new(CancellableInput(std::fs::File::from(handle))))
    }
    #[cfg(not(unix))]
    {
        Ok(Box::new(std::io::stdout()))
    }
}

#[cfg(unix)]
struct CancellableInput(std::fs::File);

#[cfg(unix)]
impl std::io::Write for CancellableInput {
    fn write(&mut self, buffer: &[u8]) -> std::io::Result<usize> {
        use std::os::fd::AsRawFd;
        if buffer.is_empty() {
            return Ok(0);
        }
        loop {
            if cancelled() {
                return Err(std::io::Error::other("interrupted"));
            }
            let mut descriptor = libc::pollfd {
                fd: self.0.as_raw_fd(),
                events: libc::POLLOUT,
                revents: 0,
            };
            // The descriptor stays owned by self; poll receives one valid writable element.
            let ready = unsafe { libc::poll(&mut descriptor, 1, 100) };
            if ready > 0 {
                let result = self.0.write(buffer);
                return if cancelled() {
                    Err(std::io::Error::other("interrupted"))
                } else {
                    result
                };
            }
            if ready < 0 {
                let error = std::io::Error::last_os_error();
                if error.kind() != std::io::ErrorKind::Interrupted {
                    return Err(error);
                }
            }
        }
    }
    fn flush(&mut self) -> std::io::Result<()> {
        if cancelled() {
            Err(std::io::Error::other("interrupted"))
        } else {
            Ok(())
        }
    }
}

#[cfg(unix)]
impl std::io::Read for CancellableInput {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        use std::os::fd::AsRawFd;
        if buffer.is_empty() {
            return Ok(0);
        }
        loop {
            if cancelled() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::Interrupted,
                    "interrupted",
                ));
            }
            let mut descriptor = libc::pollfd {
                fd: self.0.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            // The descriptor remains owned by self, and poll receives one valid writable element.
            let ready = unsafe { libc::poll(&mut descriptor, 1, 100) };
            if ready > 0 {
                return self.0.read(buffer);
            }
            if ready < 0 {
                let error = std::io::Error::last_os_error();
                if error.kind() != std::io::ErrorKind::Interrupted {
                    return Err(error);
                }
            }
        }
    }
}

extern "C" fn on_signal(_: libc::c_int) {
    CANCELLED.store(true, Ordering::Relaxed);
}

pub fn install_signal_handlers() -> Result<(), Error> {
    #[cfg(unix)]
    {
        // The handler only sets a lock-free atomic. No allocation or I/O occurs in signal context.
        unsafe {
            let mut action: libc::sigaction = std::mem::zeroed();
            action.sa_sigaction = on_signal as *const () as usize;
            action.sa_flags = 0;
            libc::sigemptyset(&mut action.sa_mask);
            for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
                if libc::sigaction(signal, &action, std::ptr::null_mut()) != 0 {
                    return Err(Error::new(
                        3,
                        format!(
                            "cannot install signal handlers: {}",
                            std::io::Error::last_os_error()
                        ),
                    ));
                }
            }
            action.sa_sigaction = libc::SIG_IGN;
            if libc::sigaction(libc::SIGPIPE, &action, std::ptr::null_mut()) != 0 {
                return Err(Error::new(3, "cannot ignore SIGPIPE"));
            }
        }
    }
    #[cfg(windows)]
    unsafe {
        for signal in [libc::SIGINT, libc::SIGTERM] {
            if libc::signal(signal, on_signal as *const () as usize) == libc::SIG_ERR as usize {
                return Err(Error::new(3, "cannot install signal handlers"));
            }
        }
    }
    Ok(())
}
