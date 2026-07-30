use std::path::PathBuf;
use std::process::Command;

#[test]
fn accepted_builder_canonical_bytes_match_golden() {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repository = crate_root.ancestors().nth(2).expect("repository root");
    let builder = repository.join("scripts/build_native_hermetic_package.py");
    let fixture = crate_root.join("tests/fixtures/builder-canonical-sample.json");
    let script = r#"
import importlib.util,json,pathlib,sys
spec=importlib.util.spec_from_file_location("nf1_builder",sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
value=json.loads(pathlib.Path(sys.argv[2]).read_bytes())
sys.stdout.buffer.write(module._canonical_json(value))
"#;
    let output = Command::new("python3")
        .args([
            "-I",
            "-S",
            "-c",
            script,
            builder.to_str().expect("UTF-8 builder path"),
            fixture.to_str().expect("UTF-8 fixture path"),
        ])
        .output()
        .expect("run accepted NF1 builder serializer");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(output.stdout, std::fs::read(fixture).unwrap());
}

#[test]
fn accepted_builder_package_modes_match_nf2_contract() {
    let crate_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repository = crate_root.ancestors().nth(2).expect("repository root");
    let builder = repository.join("scripts/build_native_hermetic_package.py");
    let script = r#"
import importlib.util,json,pathlib,sys,tempfile
spec=importlib.util.spec_from_file_location("nf1_builder",sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as raw:
 p=pathlib.Path(raw);(p/"bin").mkdir();(p/"config").mkdir()
 (p/"bin/trustforge-native-foundation").write_bytes(b"x")
 (p/"config/fixed-config.json").write_bytes(b"{}")
 (p/"config/public-metadata-format.json").write_bytes(b"{}")
 (p/"bin").chmod(0o555);(p/"config").chmod(0o555)
 (p/"bin/trustforge-native-foundation").chmod(0o555)
 (p/"config/fixed-config.json").chmod(0o444)
 (p/"config/public-metadata-format.json").chmod(0o444)
 print(json.dumps({e["path"]:e["mode"] for e in module._package_entries(p)},sort_keys=True))
"#;
    let output = Command::new("python3")
        .args([
            "-I",
            "-S",
            "-c",
            script,
            builder.to_str().expect("UTF-8 builder path"),
        ])
        .output()
        .expect("run accepted NF1 package entry helper");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(
        String::from_utf8(output.stdout).unwrap().trim(),
        r#"{"bin": "0555", "bin/trustforge-native-foundation": "0555", "config": "0555", "config/fixed-config.json": "0444", "config/public-metadata-format.json": "0444"}"#
    );
}
