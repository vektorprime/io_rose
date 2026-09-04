"""Regression tests for the EFT/PTL effect parsers (rose/eft.py, rose/ptl.py).

- Every .EFT in the client's 3Ddata/EFFECT directory saves back
  byte-identically (covers skip blobs, padding, 0xCD debug fill flags,
  NUL-terminated VFS strings).
- Every .PTL in 3Ddata/EFFECT/PARTICLES saves back byte-identically.
- Spot checks mirror the Rust client (rose-file-readers eft.rs / ptl.rs):
  effective-path rules (flag + empty/"NULL"), blend tables, keyframe ids.

Exit code 0 on success, 1 on failure.
"""
import io
import os
import sys

ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ADDON_ROOT)

import _paths  # noqa: E402

from rose.eft import Eft  # noqa: E402
from rose.ptl import Ptl  # noqa: E402

EFFECT_DIR = os.path.join(_paths.client_3ddata_root(), "EFFECT")
PARTICLE_DIR = os.path.join(EFFECT_DIR, "PARTICLES")


def _roundtrip(parse_cls, path):
    with open(path, "rb") as f:
        original = f.read()
    obj = parse_cls(path)
    buf = io.BytesIO()
    obj.write(buf)
    return original, buf.getvalue()


def test_eft_roundtrip_all():
    files = sorted(f for f in os.listdir(EFFECT_DIR) if f.upper().endswith(".EFT"))
    assert files, f"no .EFT files in {EFFECT_DIR}"
    bad = []
    for name in files:
        original, saved = _roundtrip(Eft, os.path.join(EFFECT_DIR, name))
        if saved != original:
            bad.append(name)
    assert not bad, f"EFT roundtrip mismatch: {bad[:10]}"
    print(f"EFT roundtrip: {len(files)}/{len(files)} byte-identical")
    return files


def test_ptl_roundtrip_all():
    files = sorted(f for f in os.listdir(PARTICLE_DIR) if f.upper().endswith(".PTL"))
    assert files, f"no .PTL files in {PARTICLE_DIR}"
    bad = []
    for name in files:
        original, saved = _roundtrip(Ptl, os.path.join(PARTICLE_DIR, name))
        if saved != original:
            bad.append(name)
    assert not bad, f"PTL roundtrip mismatch: {bad[:10]}"
    print(f"PTL roundtrip: {len(files)}/{len(files)} byte-identical")
    return files


def test_effective_paths():
    # TEST.EFT: particle with no anim (NULL + garbage flag), mesh with a
    # morph anim but no transform anim. Mirrors eft.rs effective-path rules.
    eft = Eft(os.path.join(EFFECT_DIR, "TEST.EFT"))
    assert len(eft.particles) == 1, len(eft.particles)
    assert len(eft.meshes) == 1, len(eft.meshes)
    particle = eft.particles[0]
    assert particle.particle_path().upper().endswith("_FIREBULLET_FIRING_01.PTL")
    assert particle.animation_path() == "", repr(particle.animation_path())
    mesh = eft.meshes[0]
    assert mesh.mesh_file.upper().endswith("_HOKE_01.ZMS"), mesh.mesh_file
    assert mesh.mesh_animation_path().upper().endswith("_HOKE_01.ZMO")
    assert mesh.texture_path().upper().endswith("_HOKE_01.DDS")
    assert mesh.animation_path() == ""
    assert eft.sound_path() == ""
    print("effective paths: TEST.EFT ok")


def test_ptl_sequences():
    ptl = Ptl(os.path.join(PARTICLE_DIR, "_firebullet_firing_01.PTL"))
    assert ptl.sequences, "expected at least one sequence"
    for seq in ptl.sequences:
        assert seq.num_particles > 0, seq.name
        assert seq.align_type in (0, 1, 2), seq.align_type
        for kf in seq.keyframes:
            assert kf.keyframe_type in range(1, 14), kf.keyframe_type
    print(f"PTL sequences: {os.path.basename('_firebullet_firing_01.PTL')} "
          f"({len(ptl.sequences)} sequences) ok")


def main():
    test_eft_roundtrip_all()
    test_ptl_roundtrip_all()
    test_effective_paths()
    test_ptl_sequences()
    print("ALL EFT TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
