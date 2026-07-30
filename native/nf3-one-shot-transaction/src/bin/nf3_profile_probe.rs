use trustforge_nf3_one_shot_transaction::{accepted_build_identity, accepted_foundation_sha256};

fn main() {
    let Ok(identity) = accepted_build_identity() else {
        eprintln!("UNBOUND_BUILD_PROFILE");
        std::process::exit(77);
    };
    let foundation = accepted_foundation_sha256().expect("bound identity changed");
    println!(
        "BOUND_PROFILE profile={} source={} rlib={} profile_receipt={} foundation={foundation}",
        identity.profile,
        identity.linked_source_sha256,
        identity.linked_rlib_sha256,
        identity.profile_receipt_sha256,
    );
}
