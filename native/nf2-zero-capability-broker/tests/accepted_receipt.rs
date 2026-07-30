#[test]
fn externally_rebuilt_nf1_manifest_matches_compile_time_receipt() {
    let bytes = include_bytes!("fixtures/accepted-native-hermetic-provenance.json");
    trustforge_nf2_zero_capability_broker::manifest::validate_accepted(bytes)
        .expect("supplied NF1 manifest must match accepted compile-time receipt");
}
