use super::Request;
use crate::common::Error;
use std::ffi::OsString;

pub fn parse(_args: impl IntoIterator<Item = OsString>) -> Result<Option<Request>, Error> {
    Err(Error::new(1, "LCOV compatibility implementation in progress"))
}
