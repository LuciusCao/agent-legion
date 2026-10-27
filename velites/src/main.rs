use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    let args = std::env::args().collect();
    velites::entrypoint::run(args).await
}
