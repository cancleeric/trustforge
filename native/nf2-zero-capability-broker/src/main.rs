use trustforge_nf2_zero_capability_broker::{BLOCKED_EXTERNAL_LINUX, Outcome};

fn main() {
    match trustforge_nf2_zero_capability_broker::run() {
        Ok(Outcome::Completed) => {}
        Ok(Outcome::BlockedExternalLinux) => {
            eprintln!("BLOCKED_EXTERNAL_LINUX: NF2 requires Linux x86_64 kernel evidence");
            std::process::exit(BLOCKED_EXTERNAL_LINUX);
        }
        Err(error) => {
            eprintln!("BLOCK: {error}");
            std::process::exit(70);
        }
    }
}
