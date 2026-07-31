use crate::canonical_json::{self, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const RUNTIME_PATH: &str = "bin/trustforge-native-foundation";
const SCHEMA: &str = "trustforge.native-hermetic-provenance/v1";
pub const ACCEPTED_COMMIT: &str = "e28a675f03ee517dcd69fba0d7705ec8828d24cd";
pub const ACCEPTED_TREE: &str = "9a912277b3458c54462a8a6101db8e4766038a1f";
pub const ACCEPTED_MANIFEST_SHA256: &str =
    "5e2db7cf733482a0c43bbfe2a27e96c3b255c1a69dde32054db3181a92fd241c";
pub const ACCEPTED_RUNTIME_SHA256: &str =
    "cf8c2165cb93b7a8712d848b653d51a977f4ce12f1a9dad7bd41e189ee694f86";
pub const ACCEPTED_ARCHIVE_SHA256: &str =
    "808487c590a183a8df2e69cfc5257969e18ae88b15c4378da95d97add6c03c1b";
const RUSTFLAGS: &str = "--remap-path-prefix=/build-input=/workspace/native/hermetic-package -C relocation-model=static -C link-arg=--build-id=none -C link-arg=-no-pie";

/// Compile-time identity pins for an accepted NF1 artifact.
///
/// Every field is `&'static str` (or a static slice) so the struct is
/// const-constructible. `NF1_PINS` is the sole blessed instance; the public
/// `validate_accepted` hard-codes it internally — callers cannot inject an
/// alternative pin set (authority-injection defense).
#[derive(Debug, Clone, Eq, PartialEq)]
pub struct CargoPackage {
    pub name: &'static str,
    pub version: &'static str,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct GeneratedInput {
    pub path: &'static str,
    pub recipe: &'static str,
}

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct AcceptedPins {
    pub schema: &'static str,
    pub runtime_path: &'static str,
    pub entry_paths: &'static [&'static str],
    pub entry_cardinality: usize,
    pub cargo_package: CargoPackage,
    pub generated: GeneratedInput,
    pub runtime_closure_method: &'static str,
    pub commit: &'static str,
    pub tree: &'static str,
    pub manifest_sha256: &'static str,
    pub runtime_sha256: &'static str,
    pub archive_sha256: &'static str,
}

pub const NF1_PINS: AcceptedPins = AcceptedPins {
    schema: SCHEMA,
    runtime_path: RUNTIME_PATH,
    entry_paths: &[
        "bin",
        "config",
        RUNTIME_PATH,
        "config/fixed-config.json",
        "config/public-metadata-format.json",
    ],
    entry_cardinality: 5,
    cargo_package: CargoPackage {
        name: "trustforge-native-foundation",
        version: "0.1.0",
    },
    generated: GeneratedInput {
        path: "generated/source_epoch.rs",
        recipe: "scripts/build_native_hermetic_package.py:EPOCH",
    },
    runtime_closure_method: "bounds-checked-elf64-parser/v1",
    commit: ACCEPTED_COMMIT,
    tree: ACCEPTED_TREE,
    manifest_sha256: ACCEPTED_MANIFEST_SHA256,
    runtime_sha256: ACCEPTED_RUNTIME_SHA256,
    archive_sha256: ACCEPTED_ARCHIVE_SHA256,
};

#[derive(Debug, Clone, Eq, PartialEq)]
pub struct RuntimeBinding {
    pub sha256: [u8; 32],
    pub size: u64,
    pub mode: u32,
    pub source_epoch: String,
}

pub fn validate(bytes: &[u8], pins: &AcceptedPins) -> Result<RuntimeBinding, &'static str> {
    let parsed = canonical_json::parse(bytes)?;
    let root = object(&parsed)?;
    let expected_top = BTreeSet::from([
        "build",
        "builder_runtime",
        "cargo_resolution",
        "environment",
        "generated",
        "package_entries",
        "runtime_closure",
        "schema",
        "sources",
        "toolchain",
        "vcs",
    ]);
    if root.keys().map(String::as_str).collect::<BTreeSet<_>>() != expected_top {
        return Err("manifest top-level schema is not exact");
    }
    if string(required(root, "schema")?)? != pins.schema {
        return Err("manifest schema enum mismatch");
    }
    validate_nested_schema(root, pins)?;
    reject_authority_metadata(&parsed)?;
    let entries = array(required(root, "package_entries")?)?;
    if entries.len() != pins.entry_cardinality {
        return Err("package entry cardinality mismatch");
    }
    let expected_paths: BTreeSet<&str> = pins.entry_paths.iter().copied().collect();
    let mut paths = BTreeSet::new();
    let mut runtime = None;
    for value in entries {
        let entry = object(value)?;
        let path = string(required(entry, "path")?)?;
        if !paths.insert(path) {
            return Err("duplicate package path");
        }
        let kind = string(required(entry, "type")?)?;
        let expected_keys = if kind == "directory" {
            BTreeSet::from(["mode", "path", "type"])
        } else if kind == "file" {
            BTreeSet::from(["mode", "path", "sha256", "size", "type"])
        } else {
            return Err("package entry type mismatch");
        };
        if entry.keys().map(String::as_str).collect::<BTreeSet<_>>() != expected_keys {
            return Err("package entry schema is not exact");
        }
        let mode = parse_mode(string(required(entry, "mode")?)?)?;
        if ((kind == "directory" || path == pins.runtime_path) && mode != 0o555)
            || (kind == "file" && path != pins.runtime_path && mode != 0o444)
        {
            return Err("package entry mode mismatch");
        }
        if kind == "file" {
            let size = integer(required(entry, "size")?)?;
            let digest = parse_digest(string(required(entry, "sha256")?)?)?;
            if path == pins.runtime_path {
                runtime = Some(RuntimeBinding {
                    sha256: digest,
                    size,
                    mode,
                    source_epoch: String::new(),
                });
            }
        }
    }
    if paths != expected_paths {
        return Err("package path set mismatch");
    }
    let mut runtime = runtime.ok_or("runtime package entry absent")?;
    runtime.source_epoch = string(required(
        object(required(root, "environment")?)?,
        "SOURCE_DATE_EPOCH",
    )?)?
    .to_owned();
    Ok(runtime)
}

pub fn validate_accepted(bytes: &[u8]) -> Result<RuntimeBinding, &'static str> {
    let binding = validate(bytes, &NF1_PINS)?;
    let parsed = canonical_json::parse(bytes)?;
    let root = object(&parsed)?;
    let vcs = object(required(root, "vcs")?)?;
    verify_pin_values(
        crate::sha256::digest(bytes),
        binding.sha256,
        string(required(vcs, "commit")?)?,
        string(required(vcs, "tree")?)?,
        &NF1_PINS,
    )?;
    Ok(binding)
}

fn verify_pin_values(
    manifest_digest: [u8; 32],
    runtime_digest: [u8; 32],
    commit: &str,
    tree: &str,
    pins: &AcceptedPins,
) -> Result<(), &'static str> {
    if manifest_digest != parse_digest(pins.manifest_sha256)?
        || runtime_digest != parse_digest(pins.runtime_sha256)?
        || commit != pins.commit
        || tree != pins.tree
    {
        return Err("NF1 artifact differs from compile-time accepted receipt");
    }
    Ok(())
}

fn validate_nested_schema(
    root: &BTreeMap<String, Value>,
    pins: &AcceptedPins,
) -> Result<(), &'static str> {
    exact_object(required(root, "vcs")?, &["commit", "tree"])?;
    for field in ["commit", "tree"] {
        lowercase_hex(
            string(required(object(required(root, "vcs")?)?, field)?)?,
            40,
        )?;
    }
    digest_entries(required(root, "sources")?, false, true)?;

    let cargo = exact_object(
        required(root, "cargo_resolution")?,
        &["packages", "third_party_dependencies", "vendor_entries"],
    )?;
    let packages = array(required(cargo, "packages")?)?;
    if packages.len() != 1 {
        return Err("Cargo package cardinality mismatch");
    }
    let package = exact_object(&packages[0], &["name", "version"])?;
    if string(required(package, "name")?)? != pins.cargo_package.name
        || string(required(package, "version")?)? != pins.cargo_package.version
        || !array(required(cargo, "third_party_dependencies")?)?.is_empty()
        || !array(required(cargo, "vendor_entries")?)?.is_empty()
    {
        return Err("Cargo resolution mismatch");
    }

    let generated = exact_object(
        required(root, "generated")?,
        &["path", "recipe", "sha256", "size"],
    )?;
    if string(required(generated, "path")?)? != pins.generated.path
        || string(required(generated, "recipe")?)? != pins.generated.recipe
    {
        return Err("generated input enum mismatch");
    }
    lowercase_hex(string(required(generated, "sha256")?)?, 64)?;
    if integer(required(generated, "size")?)? == 0 {
        return Err("generated input is empty");
    }

    validate_toolchain(required(root, "toolchain")?)?;
    validate_builder_runtime(required(root, "builder_runtime")?)?;
    validate_environment(required(root, "environment")?)?;
    validate_build(required(root, "build")?)?;

    let closure = exact_object(
        required(root, "runtime_closure")?,
        &["dt_needed", "method", "pt_interp"],
    )?;
    if string(required(closure, "method")?)? != pins.runtime_closure_method
        || required(closure, "pt_interp")? != &Value::Bool(false)
        || !array(required(closure, "dt_needed")?)?.is_empty()
    {
        return Err("runtime closure mismatch");
    }
    Ok(())
}

fn validate_toolchain(value: &Value) -> Result<(), &'static str> {
    let toolchain = exact_object(
        value,
        &[
            "cargo",
            "host_platform",
            "host_sysroot_entries",
            "linker",
            "rustc",
            "rustup",
            "target",
            "target_libdir_entries",
        ],
    )?;
    if string(required(toolchain, "target")?)? != "x86_64-unknown-linux-musl" {
        return Err("toolchain target mismatch");
    }
    for (name, expected_name) in [
        ("cargo", "cargo"),
        ("linker", "rust-lld"),
        ("rustc", "rustc"),
        ("rustup", "rustup"),
    ] {
        let record = exact_object(
            required(toolchain, name)?,
            &["name", "sha256", "size", "version"],
        )?;
        if string(required(record, "name")?)? != expected_name {
            return Err("toolchain tool name mismatch");
        }
        string(required(record, "version")?)?;
        lowercase_hex(string(required(record, "sha256")?)?, 64)?;
        if integer(required(record, "size")?)? == 0 {
            return Err("toolchain tool is empty");
        }
    }
    digest_entries(required(toolchain, "host_sysroot_entries")?, false, true)?;
    digest_entries(required(toolchain, "target_libdir_entries")?, false, true)?;
    let host = exact_object(
        required(toolchain, "host_platform")?,
        &["kernel", "os_build"],
    )?;
    string(required(host, "kernel")?)?;
    string(required(host, "os_build")?)?;
    Ok(())
}

fn validate_builder_runtime(value: &Value) -> Result<(), &'static str> {
    let runtime = exact_object(
        value,
        &[
            "dyld_cache",
            "dyld_cache_map",
            "dyld_subcache",
            "dynamic_dependencies",
            "python_entries",
        ],
    )?;
    digest_entries(required(runtime, "python_entries")?, true, true)?;
    for item in array(required(runtime, "dynamic_dependencies")?)? {
        let value = string(item)?;
        if !(value.starts_with('/') || value.starts_with('@')) || value.contains('\n') {
            return Err("dynamic dependency invalid");
        }
    }
    validate_dyld(
        required(runtime, "dyld_cache_map")?,
        &["mode", "path", "sha256", "size"],
        None,
        "dyld_shared_cache_arm64e.map",
    )?;
    validate_dyld(
        required(runtime, "dyld_cache")?,
        &[
            "code_directory_sha256",
            "mode",
            "path",
            "sha256",
            "size",
            "uuid",
        ],
        Some(32),
        "dyld_shared_cache_arm64e",
    )?;
    validate_dyld(
        required(runtime, "dyld_subcache")?,
        &["code_directory_sha256", "mode", "path", "sha256", "size"],
        None,
        "dyld_shared_cache_arm64e.01",
    )
}

fn validate_dyld(
    value: &Value,
    keys: &[&str],
    uuid_length: Option<usize>,
    expected_path: &str,
) -> Result<(), &'static str> {
    let record = exact_object(value, keys)?;
    if string(required(record, "path")?)? != expected_path
        || string(required(record, "mode")?)? != "0755"
        || integer(required(record, "size")?)? == 0
    {
        return Err("dyld record enum mismatch");
    }
    lowercase_hex(string(required(record, "sha256")?)?, 64)?;
    if record.contains_key("code_directory_sha256") {
        lowercase_hex(string(required(record, "code_directory_sha256")?)?, 64)?;
    }
    if let Some(length) = uuid_length {
        lowercase_hex(string(required(record, "uuid")?)?, length)?;
    }
    Ok(())
}

fn validate_environment(value: &Value) -> Result<(), &'static str> {
    const KEYS: [&str; 14] = [
        "CARGO_HOME",
        "CARGO_INCREMENTAL",
        "CARGO_NET_OFFLINE",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "RUSTC",
        "RUSTFLAGS",
        "RUSTUP_TOOLCHAIN",
        "SOURCE_DATE_EPOCH",
        "TZ",
        "WRAPPER_AND_AMBIENT_KNOBS",
    ];
    let environment = object(value)?;
    let expected = KEYS.into_iter().collect::<BTreeSet<_>>();
    if environment
        .keys()
        .map(String::as_str)
        .collect::<BTreeSet<_>>()
        != expected
    {
        return Err("environment schema mismatch");
    }
    for item in environment.values() {
        string(item)?;
    }
    for (key, expected) in [
        ("PATH", "toolchain:bin-only"),
        ("HOME", "isolated:non-user-empty-home"),
        ("CARGO_HOME", "isolated:fresh-empty-cargo-home"),
        ("RUSTC", "toolchain:locked-rustc"),
        ("TZ", "UTC"),
        ("LC_ALL", "C"),
        ("LANG", "C"),
        ("CARGO_INCREMENTAL", "0"),
        ("CARGO_NET_OFFLINE", "true"),
        (
            "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER",
            "toolchain:locked-rust-lld",
        ),
        ("RUSTUP_TOOLCHAIN", "1.96.0"),
        (
            "WRAPPER_AND_AMBIENT_KNOBS",
            "rejected:not-in-subprocess-environment",
        ),
    ] {
        if string(required(environment, key)?)? != expected {
            return Err("environment enum mismatch");
        }
    }
    if string(required(environment, "SOURCE_DATE_EPOCH")?)? != "1700000000"
        || string(required(environment, "RUSTFLAGS")?)? != RUSTFLAGS
    {
        return Err("build environment binding mismatch");
    }
    Ok(())
}

fn validate_build(value: &Value) -> Result<(), &'static str> {
    let build = exact_object(
        value,
        &["argv", "frozen", "locked", "offline", "rustflags", "target"],
    )?;
    if string(required(build, "target")?)? != "x86_64-unknown-linux-musl"
        || required(build, "frozen")? != &Value::Bool(true)
        || required(build, "locked")? != &Value::Bool(true)
        || required(build, "offline")? != &Value::Bool(true)
    {
        return Err("build enum mismatch");
    }
    if string(required(build, "rustflags")?)? != RUSTFLAGS {
        return Err("build rustflags mismatch");
    }
    let argv = array(required(build, "argv")?)?
        .iter()
        .map(string)
        .collect::<Result<Vec<_>, _>>()?;
    if argv
        != [
            "cargo",
            "build",
            "--manifest-path",
            "/build-input/Cargo.toml",
            "--release",
            "--target",
            "x86_64-unknown-linux-musl",
            "--target-dir",
            "/build-output",
            "--locked",
            "--offline",
            "--frozen",
        ]
    {
        return Err("build argv invalid");
    }
    Ok(())
}

fn digest_entries(
    value: &Value,
    allow_absolute: bool,
    require_nonempty: bool,
) -> Result<(), &'static str> {
    let entries = array(value)?;
    if require_nonempty && entries.is_empty() {
        return Err("digest entry cardinality mismatch");
    }
    let mut paths = BTreeSet::new();
    for value in entries {
        let entry = exact_object(value, &["mode", "path", "sha256", "size"])?;
        let path = string(required(entry, "path")?)?;
        if (!allow_absolute && path.starts_with('/'))
            || path.split('/').any(|part| part == "..")
            || path.contains('\n')
            || !paths.insert(path)
        {
            return Err("digest entry path invalid");
        }
        parse_mode(string(required(entry, "mode")?)?)?;
        integer(required(entry, "size")?)?;
        lowercase_hex(string(required(entry, "sha256")?)?, 64)?;
    }
    Ok(())
}

fn exact_object<'a>(
    value: &'a Value,
    keys: &[&str],
) -> Result<&'a BTreeMap<String, Value>, &'static str> {
    let value = object(value)?;
    if value.keys().map(String::as_str).collect::<BTreeSet<_>>()
        != keys.iter().copied().collect::<BTreeSet<_>>()
    {
        return Err("nested manifest schema is not exact");
    }
    Ok(value)
}

fn lowercase_hex(value: &str, length: usize) -> Result<(), &'static str> {
    if value.len() != length
        || !value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    {
        return Err("manifest lowercase hex invalid");
    }
    Ok(())
}

fn reject_authority_metadata(value: &Value) -> Result<(), &'static str> {
    const FORBIDDEN: [&str; 14] = [
        "actor",
        "eligible",
        "eligibility",
        "key",
        "key_id",
        "pass",
        "private_key",
        "publication_authority",
        "raw_key",
        "raw_public_key",
        "release_eligibility",
        "signer",
        "trust_anchor",
        "verdict",
    ];
    match value {
        Value::Object(values) => {
            for (key, value) in values {
                if !allowed_manifest_key(key) || authority_alias(key, &FORBIDDEN) {
                    return Err("authority metadata forbidden");
                }
                reject_authority_metadata(value)?;
            }
        }
        Value::Array(values) => {
            for value in values {
                reject_authority_metadata(value)?;
            }
        }
        Value::String(value) if authority_alias(value, &FORBIDDEN) => {
            return Err("authority metadata value forbidden");
        }
        _ => {}
    }
    Ok(())
}

fn authority_alias(value: &str, forbidden: &[&str]) -> bool {
    let mut snake = String::new();
    let mut previous_lower_or_digit = false;
    for character in value.chars() {
        if character.is_ascii_alphanumeric() {
            if character.is_ascii_uppercase() && previous_lower_or_digit {
                snake.push('_');
            }
            snake.push(character.to_ascii_lowercase());
            previous_lower_or_digit = character.is_ascii_lowercase() || character.is_ascii_digit();
        } else {
            if !snake.ends_with('_') && !snake.is_empty() {
                snake.push('_');
            }
            previous_lower_or_digit = false;
        }
    }
    let snake = snake.trim_matches('_');
    let padded = format!("_{snake}_");
    forbidden
        .iter()
        .any(|term| padded.contains(&format!("_{term}_")))
}

fn allowed_manifest_key(value: &str) -> bool {
    const ALLOWED: [&str; 71] = [
        "CARGO_HOME",
        "CARGO_INCREMENTAL",
        "CARGO_NET_OFFLINE",
        "CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER",
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "RUSTC",
        "RUSTFLAGS",
        "RUSTUP_TOOLCHAIN",
        "SOURCE_DATE_EPOCH",
        "TZ",
        "WRAPPER_AND_AMBIENT_KNOBS",
        "argv",
        "build",
        "builder_runtime",
        "cargo",
        "cargo_resolution",
        "checksum",
        "code_directory_sha256",
        "commit",
        "dt_needed",
        "dynamic_dependencies",
        "dyld_cache",
        "dyld_cache_map",
        "dyld_subcache",
        "environment",
        "frozen",
        "generated",
        "host_platform",
        "host_sysroot_entries",
        "kernel",
        "linker",
        "locked",
        "method",
        "mode",
        "name",
        "offline",
        "os_build",
        "package_entries",
        "packages",
        "path",
        "pt_interp",
        "python_entries",
        "recipe",
        "runtime_closure",
        "rustc",
        "rustflags",
        "rustup",
        "schema",
        "sha256",
        "size",
        "source",
        "sources",
        "target",
        "target_libdir_entries",
        "third_party_dependencies",
        "toolchain",
        "tree",
        "type",
        "uuid",
        "vcs",
        "vendor_entries",
        "version",
        "cargo",
        "rustc",
        "rustup",
        "linker",
        "target",
        "path",
    ];
    ALLOWED.contains(&value)
}

fn required<'a>(object: &'a BTreeMap<String, Value>, key: &str) -> Result<&'a Value, &'static str> {
    object.get(key).ok_or("required manifest field absent")
}

fn object(value: &Value) -> Result<&BTreeMap<String, Value>, &'static str> {
    match value {
        Value::Object(value) => Ok(value),
        _ => Err("manifest object type mismatch"),
    }
}

fn array(value: &Value) -> Result<&[Value], &'static str> {
    match value {
        Value::Array(value) => Ok(value),
        _ => Err("manifest array type mismatch"),
    }
}

fn string(value: &Value) -> Result<&str, &'static str> {
    match value {
        Value::String(value) => Ok(value),
        _ => Err("manifest string type mismatch"),
    }
}

fn integer(value: &Value) -> Result<u64, &'static str> {
    match value {
        Value::Number(value) => value.parse().map_err(|_| "manifest integer invalid"),
        _ => Err("manifest integer type mismatch"),
    }
}

fn parse_mode(value: &str) -> Result<u32, &'static str> {
    if value.len() != 4 || !value.bytes().all(|byte| matches!(byte, b'0'..=b'7')) {
        return Err("manifest mode invalid");
    }
    u32::from_str_radix(value, 8).map_err(|_| "manifest mode invalid")
}

fn parse_digest(value: &str) -> Result<[u8; 32], &'static str> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    {
        return Err("manifest digest invalid");
    }
    let mut digest = [0_u8; 32];
    for (index, output) in digest.iter_mut().enumerate() {
        *output = u8::from_str_radix(&value[index * 2..index * 2 + 2], 16)
            .map_err(|_| "manifest digest invalid")?;
    }
    Ok(digest)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(runtime_digest: &str) -> Vec<u8> {
        let digest = "a".repeat(64);
        let entry = format!(
            "{{\"mode\":\"0444\",\"path\":\"Cargo.toml\",\"sha256\":\"{digest}\",\"size\":1}}"
        );
        let tool = |name: &str| {
            format!("{{\"name\":\"{name}\",\"sha256\":\"{digest}\",\"size\":1,\"version\":\"1\"}}")
        };
        let cargo_tool = tool("cargo");
        let linker_tool = tool("rust-lld");
        let rustc_tool = tool("rustc");
        let rustup_tool = tool("rustup");
        format!(
            "{{\"build\":{{\"argv\":[\"cargo\",\"build\",\"--manifest-path\",\"/build-input/Cargo.toml\",\"--release\",\"--target\",\"x86_64-unknown-linux-musl\",\"--target-dir\",\"/build-output\",\"--locked\",\"--offline\",\"--frozen\"],\"frozen\":true,\"locked\":true,\"offline\":true,\"rustflags\":\"{RUSTFLAGS}\",\"target\":\"x86_64-unknown-linux-musl\"}},\"builder_runtime\":{{\"dyld_cache\":{{\"code_directory_sha256\":\"{digest}\",\"mode\":\"0755\",\"path\":\"dyld_shared_cache_arm64e\",\"sha256\":\"{digest}\",\"size\":1,\"uuid\":\"{}\"}},\"dyld_cache_map\":{{\"mode\":\"0755\",\"path\":\"dyld_shared_cache_arm64e.map\",\"sha256\":\"{digest}\",\"size\":1}},\"dyld_subcache\":{{\"code_directory_sha256\":\"{digest}\",\"mode\":\"0755\",\"path\":\"dyld_shared_cache_arm64e.01\",\"sha256\":\"{digest}\",\"size\":1}},\"dynamic_dependencies\":[\"/usr/lib/libSystem.B.dylib\"],\"python_entries\":[{entry}]}},\"cargo_resolution\":{{\"packages\":[{{\"name\":\"trustforge-native-foundation\",\"version\":\"0.1.0\"}}],\"third_party_dependencies\":[],\"vendor_entries\":[]}},\"environment\":{{\"CARGO_HOME\":\"isolated:fresh-empty-cargo-home\",\"CARGO_INCREMENTAL\":\"0\",\"CARGO_NET_OFFLINE\":\"true\",\"CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER\":\"toolchain:locked-rust-lld\",\"HOME\":\"isolated:non-user-empty-home\",\"LANG\":\"C\",\"LC_ALL\":\"C\",\"PATH\":\"toolchain:bin-only\",\"RUSTC\":\"toolchain:locked-rustc\",\"RUSTFLAGS\":\"{RUSTFLAGS}\",\"RUSTUP_TOOLCHAIN\":\"1.96.0\",\"SOURCE_DATE_EPOCH\":\"1700000000\",\"TZ\":\"UTC\",\"WRAPPER_AND_AMBIENT_KNOBS\":\"rejected:not-in-subprocess-environment\"}},\"generated\":{{\"path\":\"generated/source_epoch.rs\",\"recipe\":\"scripts/build_native_hermetic_package.py:EPOCH\",\"sha256\":\"{digest}\",\"size\":1}},\"package_entries\":[{{\"mode\":\"0555\",\"path\":\"bin\",\"type\":\"directory\"}},{{\"mode\":\"0555\",\"path\":\"config\",\"type\":\"directory\"}},{{\"mode\":\"0555\",\"path\":\"bin/trustforge-native-foundation\",\"sha256\":\"{runtime_digest}\",\"size\":42,\"type\":\"file\"}},{{\"mode\":\"0444\",\"path\":\"config/fixed-config.json\",\"sha256\":\"{}\",\"size\":1,\"type\":\"file\"}},{{\"mode\":\"0444\",\"path\":\"config/public-metadata-format.json\",\"sha256\":\"{}\",\"size\":1,\"type\":\"file\"}}],\"runtime_closure\":{{\"dt_needed\":[],\"method\":\"bounds-checked-elf64-parser/v1\",\"pt_interp\":false}},\"schema\":\"trustforge.native-hermetic-provenance/v1\",\"sources\":[{entry}],\"toolchain\":{{\"cargo\":{cargo_tool},\"host_platform\":{{\"kernel\":\"Darwin 25 arm64\",\"os_build\":\"A\"}},\"host_sysroot_entries\":[{entry}],\"linker\":{linker_tool},\"rustc\":{rustc_tool},\"rustup\":{rustup_tool},\"target\":\"x86_64-unknown-linux-musl\",\"target_libdir_entries\":[{entry}]}},\"vcs\":{{\"commit\":\"{}\",\"tree\":\"{}\"}}}}\n",
            "b".repeat(32),
            "1".repeat(64),
            "2".repeat(64),
            "3".repeat(40),
            "4".repeat(40),
        )
        .into_bytes()
    }

    #[test]
    fn extracts_exact_runtime_binding() {
        let digest = "01".repeat(32);
        let binding = validate(&fixture(&digest), &NF1_PINS).unwrap();
        assert_eq!(binding.sha256, [1; 32]);
        assert_eq!(binding.size, 42);
        assert_eq!(binding.mode, 0o555);
    }

    #[test]
    fn rejects_malformed_digest_and_duplicate_path() {
        assert!(validate(&fixture(&"g".repeat(64)), &NF1_PINS).is_err());
        let duplicate = String::from_utf8(fixture(&"0".repeat(64)))
            .unwrap()
            .replace("\"path\":\"config\"", "\"path\":\"bin\"");
        assert!(validate(duplicate.as_bytes(), &NF1_PINS).is_err());
    }

    #[test]
    fn rejects_nested_schema_drift_and_authority_alias() {
        let valid = String::from_utf8(fixture(&"0".repeat(64))).unwrap();
        assert!(
            validate(
                valid
                    .replace("\"offline\":true", "\"offline\":false")
                    .as_bytes(),
                &NF1_PINS
            )
            .is_err()
        );
        for key in ["signer", "keyId", "rawKey", "trust-anchor"] {
            let mutation = valid.replace("\"build\":{", &format!("\"build\":{{\"{key}\":\"x\","));
            assert!(validate(mutation.as_bytes(), &NF1_PINS).is_err(), "{key}");
        }
        let duplicate_source = valid.replace(
            "\"sources\":[{\"mode\"",
            "\"sources\":[{\"mode\":\"0444\",\"path\":\"Cargo.toml\",\"sha256\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"size\":1},{\"mode\"",
        );
        assert!(validate(duplicate_source.as_bytes(), &NF1_PINS).is_err());
    }

    #[test]
    fn accepted_receipt_rejects_every_identity_mutation() {
        let manifest = parse_digest(ACCEPTED_MANIFEST_SHA256).unwrap();
        let runtime = parse_digest(ACCEPTED_RUNTIME_SHA256).unwrap();
        assert!(
            verify_pin_values(manifest, runtime, ACCEPTED_COMMIT, ACCEPTED_TREE, &NF1_PINS).is_ok()
        );
        let mut changed_manifest = manifest;
        changed_manifest[0] ^= 1;
        assert!(verify_pin_values(
            changed_manifest,
            runtime,
            ACCEPTED_COMMIT,
            ACCEPTED_TREE,
            &NF1_PINS
        )
        .is_err());
        let mut changed_runtime = runtime;
        changed_runtime[0] ^= 1;
        assert!(verify_pin_values(
            manifest,
            changed_runtime,
            ACCEPTED_COMMIT,
            ACCEPTED_TREE,
            &NF1_PINS
        )
        .is_err());
        assert!(
            verify_pin_values(manifest, runtime, &"0".repeat(40), ACCEPTED_TREE, &NF1_PINS)
                .is_err()
        );
        assert!(
            verify_pin_values(manifest, runtime, ACCEPTED_COMMIT, &"0".repeat(40), &NF1_PINS)
                .is_err()
        );
    }

    #[test]
    fn nf1_accepted_manifest_validates_through_pins_and_accepted() {
        let bytes = include_bytes!("../tests/fixtures/accepted-native-hermetic-provenance.json");
        assert!(validate(bytes, &NF1_PINS).is_ok());
        assert!(validate_accepted(bytes).is_ok());
    }

    #[test]
    fn each_pin_bound_field_tampering_is_rejected_by_validate() {
        let bytes = include_bytes!("../tests/fixtures/accepted-native-hermetic-provenance.json");
        let original = std::str::from_utf8(bytes).unwrap();

        let tampered = original.replacen(
            "trustforge.native-hermetic-provenance/v1",
            "trustforge.native-hermetic-provenance/v2",
            1,
        );
        assert!(validate(tampered.as_bytes(), &NF1_PINS).is_err(), "schema");

        let tampered = original.replace(
            "\"path\":\"bin/trustforge-native-foundation\"",
            "\"path\":\"bin/different-binary\"",
        );
        assert!(
            validate(tampered.as_bytes(), &NF1_PINS).is_err(),
            "runtime_path"
        );

        let tampered = original.replacen(
            "\"name\":\"trustforge-native-foundation\"",
            "\"name\":\"different-package\"",
            1,
        );
        assert!(
            validate(tampered.as_bytes(), &NF1_PINS).is_err(),
            "cargo_name"
        );

        let tampered = original.replacen("\"version\":\"0.1.0\"", "\"version\":\"0.2.0\"", 1);
        assert!(
            validate(tampered.as_bytes(), &NF1_PINS).is_err(),
            "cargo_version"
        );

        let tampered =
            original.replacen("generated/source_epoch.rs", "generated/different.rs", 1);
        assert!(
            validate(tampered.as_bytes(), &NF1_PINS).is_err(),
            "generated_path"
        );

        let tampered = original.replacen(
            "scripts/build_native_hermetic_package.py:EPOCH",
            "scripts/build_native_hermetic_package.py:DIFFERENT",
            1,
        );
        assert!(
            validate(tampered.as_bytes(), &NF1_PINS).is_err(),
            "generated_recipe"
        );

        let tampered = original.replacen(
            "bounds-checked-elf64-parser/v1",
            "bounds-checked-elf64-parser/v2",
            1,
        );
        assert!(
            validate(tampered.as_bytes(), &NF1_PINS).is_err(),
            "closure_method"
        );
    }

    #[test]
    fn validate_accepted_rejects_byte_change_that_passes_schema() {
        let bytes = include_bytes!("../tests/fixtures/accepted-native-hermetic-provenance.json");
        assert!(validate_accepted(bytes).is_ok());
        let original = std::str::from_utf8(bytes).unwrap();
        let structural_ok = original.replacen(
            "3e416df1daec68de9bd56d50aee1a12dbbdcf87f7b8cfa4484dcdd37cc430058",
            "3e416df1daec68de9bd56d50aee1a12dbbdcf87f7b8cfa4484dcdd37cc430059",
            1,
        );
        assert!(
            validate(structural_ok.as_bytes(), &NF1_PINS).is_ok(),
            "dyld sha256 is hex-valid but not pin-bound"
        );
        assert!(
            validate_accepted(structural_ok.as_bytes()).is_err(),
            "manifest digest changed"
        );
    }
}
