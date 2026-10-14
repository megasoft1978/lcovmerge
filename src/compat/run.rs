use super::Request;
use crate::common::Error;

pub fn run(_request: Request) -> Result<(), Error> {
    Err(Error::new(1, "LCOV compatibility implementation in progress"))
}
