#[cfg(not(all(target_os = "linux", target_arch = "x86_64")))]
compile_error!("nf3-test-helper requires Linux x86_64");

use std::path::Path;
use trustforge_nf3_one_shot_transaction::{
    IntegratedRunner, LedgerStore, Request, accepted_build_identity,
};

fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let command = args.get(1).ok_or("missing command")?;
    let root = args.get(2).ok_or("missing root")?;
    if command == "provision" {
        if args.len() != 3 {
            return Err("usage: nf3-test-helper provision ROOT".into());
        }
        LedgerStore::provision_for_test(Path::new(root), &"44".repeat(32))?;
        return Ok(());
    }
    if command == "probe-blocked" {
        if args.len() != 3 {
            return Err("usage: nf3-test-helper probe-blocked ROOT".into());
        }
        return match LedgerStore::open_for_test(Path::new(root)) {
            Ok(_) => Err("store unexpectedly opened".into()),
            Err(_) => Ok(()),
        };
    }
    if command == "integrated" {
        let [_, _, _, tx, deadline] = args.as_slice() else {
            return Err("usage: nf3-test-helper integrated ROOT TX_HEX DEADLINE_NS".into());
        };
        let runner = IntegratedRunner::open_for_test(Path::new(root))?;
        let binding = runner.execute(tx, deadline.parse()?)?;
        let executor = accepted_build_identity()?;
        let store_id = std::fs::read_to_string(Path::new(root).join("store-id"))?;
        let mut records = std::fs::read_dir(Path::new(root).join("heads"))?
            .map(|entry| entry.map(|value| value.file_name()))
            .collect::<Result<Vec<_>, _>>()?;
        records.sort();
        let terminal_head = records
            .iter()
            .filter_map(|name| name.to_str())
            .rfind(|name| name.ends_with(".record"))
            .ok_or("terminal head absent")?;
        println!(
            "INTEGRATED_COMMITTED transaction={} request={} store={} terminal_head={} foundation={} boot={} deadline={} executor_profile={} executor_source={} executor_rlib={} executor_profile_receipt={}",
            binding.transaction_id,
            binding.request_sha256,
            store_id.trim_end(),
            terminal_head,
            binding.foundation_sha256,
            binding.boot_id,
            binding.deadline_boottime_ns,
            executor.profile,
            executor.linked_source_sha256,
            executor.linked_rlib_sha256,
            executor.profile_receipt_sha256,
        );
        return Ok(());
    }
    let store = LedgerStore::open_for_test(Path::new(root))?;
    let [_, _, _, tx, tag, deadline] = args.as_slice() else {
        return Err("usage: nf3-test-helper COMMAND ROOT TX_HEX TAG DEADLINE_NS".into());
    };
    if command.starts_with("pause-") {
        println!("HELPER_ARMED command={command}");
    }
    let session = store.claim(
        tx,
        Request {
            foundation_sha256: "33".repeat(32),
            operation: "execute".into(),
            payload: tag.as_bytes().to_vec(),
            deadline_boottime_ns: deadline.parse()?,
        },
    )?;
    println!("CLAIMED");
    match command.as_str() {
        "commit" | "pause-commit" => session.commit()?,
        "abandon" | "pause-abandon" => drop(session),
        "hold" => loop {
            std::thread::park();
        },
        "fork-commit" => {
            unsafe extern "C" {
                fn syscall(number: std::ffi::c_long, ...) -> std::ffi::c_long;
            }
            let pid = unsafe { syscall(57) };
            if pid < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            if pid == 0 {
                let rejected = session.commit().is_err();
                std::process::exit(if rejected { 0 } else { 90 });
            }
            let mut status = 0i32;
            if unsafe { syscall(61, pid, &mut status, 0) } < 0 {
                return Err(std::io::Error::last_os_error().into());
            }
            if status != 0 {
                return Err("forked commit was not rejected".into());
            }
            drop(session);
        }
        _ => return Err("unknown command".into()),
    }
    Ok(())
}
