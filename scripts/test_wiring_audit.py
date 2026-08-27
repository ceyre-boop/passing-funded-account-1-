"""Tests for scripts/wiring_audit.py — the built/tested/never-wired detector.

The point of this file is one test: test_no_new_unexplained_disconnects. Every
other test exercises the detection logic in isolation (small synthetic ASTs)
so a change to the detector itself is covered before it ever touches the real
repo scan.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import wiring_audit as wa  # noqa: E402


# --------------------------------------------------------------------------
# The whole point: the live repo scan must have zero un-allowlisted findings,
# and the allowlist itself must be well-formed. A new disconnect — an import
# nobody calls, an orphaned module, a field with no supplier, a broken import,
# a dead artifact — makes this FAIL until it is either fixed or allowlisted
# with a reason. That is the mechanism this whole task exists to build.
# --------------------------------------------------------------------------

def test_no_new_unexplained_disconnects():
    audit = wa.run_audit()
    unexplained = []
    for cat in wa.CATEGORIES:
        for finding in audit["results"][cat]["flagged"]:
            unexplained.append(f"[{cat}] {finding['module']}: {finding['evidence']}")
    assert not unexplained, (
        "wiring_audit found NEW, un-allowlisted disconnect(s):\n"
        + "\n".join(unexplained)
        + "\n\nEither fix the disconnect, or add a `reason` entry to "
          f"{wa.ALLOWLIST_PATH.relative_to(ROOT)} explaining why it's intentional."
    )


def test_allowlist_entries_all_have_reasons():
    allowlist = wa.load_allowlist()
    errors = wa.validate_allowlist(allowlist)
    assert not errors, "allowlist entries missing a reason:\n" + "\n".join(errors)


def test_allowlist_file_exists_and_parses():
    assert wa.ALLOWLIST_PATH.exists(), f"expected allowlist at {wa.ALLOWLIST_PATH}"
    allowlist = wa.load_allowlist()
    assert isinstance(allowlist, dict)
    for cat in wa.CATEGORIES:
        assert cat in allowlist, f"allowlist is missing the {cat} key (use an empty list if none)"


# --------------------------------------------------------------------------
# Unit coverage for the detection logic, on synthetic ASTs — these guard
# against silent regressions in the detector's own behaviour.
# --------------------------------------------------------------------------

def _pm(source: str, relpath: str, is_test: bool = False) -> wa.ParsedModule:
    path = ROOT / relpath
    tree = ast.parse(textwrap.dedent(source), filename=str(path))
    mod_name = wa.module_name_for(path)
    return wa.ParsedModule(path=path, module_name=mod_name, is_test=is_test, tree=tree, source=textwrap.dedent(source))


class TestOrphans:
    def test_module_with_no_importer_is_flagged(self):
        modules = {
            "daytrade.__synthetic_orphan__": _pm("x = 1\n", "daytrade/__synthetic_orphan__.py"),
        }
        findings = wa.find_orphans(modules, set(modules.keys()))
        assert any(f["module"] == "daytrade.__synthetic_orphan__" for f in findings)

    def test_module_imported_by_production_code_is_not_orphan(self):
        modules = {
            "daytrade.__synth_lib__": _pm("def f(): pass\n", "daytrade/__synth_lib__.py"),
            "daytrade.__synth_caller__": _pm(
                "from __synth_lib__ import f\nf()\n", "daytrade/__synth_caller__.py"
            ),
        }
        findings = wa.find_orphans(modules, set(modules.keys()))
        assert not any(f["module"] == "daytrade.__synth_lib__" for f in findings)

    def test_module_imported_only_by_test_file_is_still_orphan(self):
        modules = {
            "daytrade.__synth_lib2__": _pm("def f(): pass\n", "daytrade/__synth_lib2__.py"),
            "daytrade.test___synth_lib2__": _pm(
                "from __synth_lib2__ import f\nf()\n",
                "daytrade/test___synth_lib2__.py",
                is_test=True,
            ),
        }
        findings = wa.find_orphans(modules, set(modules.keys()))
        assert any(f["module"] == "daytrade.__synth_lib2__" for f in findings)


class TestImportedNotCalled:
    def test_import_with_no_call_is_flagged(self):
        modules = {
            "daytrade.__synth_a__": _pm("X = 1\n", "daytrade/__synth_a__.py"),
            "daytrade.__synth_b__": _pm(
                "from __synth_a__ import X\nprint(X)\n", "daytrade/__synth_b__.py"
            ),
        }
        findings = wa.find_imported_not_called(modules, set(modules.keys()))
        assert any(f["module"] == "daytrade.__synth_b__" and f["target"] == "daytrade.__synth_a__"
                   for f in findings)

    def test_import_that_is_called_is_not_flagged(self):
        modules = {
            "daytrade.__synth_c__": _pm("def f(): pass\n", "daytrade/__synth_c__.py"),
            "daytrade.__synth_d__": _pm(
                "from __synth_c__ import f\nf()\n", "daytrade/__synth_d__.py"
            ),
        }
        findings = wa.find_imported_not_called(modules, set(modules.keys()))
        assert not any(f["module"] == "daytrade.__synth_d__" for f in findings)

    def test_whole_module_import_called_via_attribute_is_not_flagged(self):
        modules = {
            "daytrade.__synth_e__": _pm("def f(): pass\n", "daytrade/__synth_e__.py"),
            "daytrade.__synth_f__": _pm(
                "import __synth_e__\n__synth_e__.f()\n", "daytrade/__synth_f__.py"
            ),
        }
        findings = wa.find_imported_not_called(modules, set(modules.keys()))
        assert not any(f["module"] == "daytrade.__synth_f__" for f in findings)


class TestNoSupplier:
    def test_field_read_never_supplied_is_flagged(self):
        src = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Thing:
                x: Optional[float] = None

            def read_it(t):
                return t.x
        """
        modules = {"daytrade.__synth_g__": _pm(src, "daytrade/__synth_g__.py")}
        findings = wa.find_no_supplier(modules)
        assert any(f["field"] == "Thing.x" for f in findings)

    def test_field_supplied_via_kwarg_is_not_flagged(self):
        src = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Thing:
                x: Optional[float] = None

            def read_it(t):
                return t.x

            def make():
                return Thing(x=1.0)
        """
        modules = {"daytrade.__synth_h__": _pm(src, "daytrade/__synth_h__.py")}
        findings = wa.find_no_supplier(modules)
        assert not any(f["field"] == "Thing.x" for f in findings)

    def test_field_supplied_only_in_test_file_is_still_flagged(self):
        prod_src = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Thing:
                x: Optional[float] = None

            def read_it(t):
                return t.x
        """
        test_src = """
            from __synth_i__ import Thing

            def test_thing():
                assert Thing(x=1.0).x == 1.0
        """
        modules = {
            "daytrade.__synth_i__": _pm(prod_src, "daytrade/__synth_i__.py"),
            "daytrade.test___synth_i__": _pm(test_src, "daytrade/test___synth_i__.py", is_test=True),
        }
        findings = wa.find_no_supplier(modules)
        assert any(f["field"] == "Thing.x" for f in findings)


class TestBrokenImport:
    def test_unresolvable_top_level_import_is_flagged(self):
        src = "import this_module_does_not_exist_anywhere_xyz\n"
        modules = {"daytrade.__synth_j__": _pm(src, "daytrade/__synth_j__.py")}
        findings = wa.find_broken_imports(modules, set(modules.keys()))
        assert any(f["module"] == "daytrade.__synth_j__" for f in findings)

    def test_guarded_import_inside_try_except_is_not_flagged(self):
        src = textwrap.dedent("""
            try:
                import this_module_does_not_exist_either_xyz
            except ImportError:
                pass
        """)
        modules = {"daytrade.__synth_k__": _pm(src, "daytrade/__synth_k__.py")}
        findings = wa.find_broken_imports(modules, set(modules.keys()))
        assert not any(f["module"] == "daytrade.__synth_k__" for f in findings)

    def test_import_nested_in_function_is_not_flagged(self):
        src = textwrap.dedent("""
            def f():
                import this_module_does_not_exist_here_xyz
        """)
        modules = {"daytrade.__synth_l__": _pm(src, "daytrade/__synth_l__.py")}
        findings = wa.find_broken_imports(modules, set(modules.keys()))
        assert not any(f["module"] == "daytrade.__synth_l__" for f in findings)

    def test_stdlib_import_is_not_flagged(self):
        src = "import json\nimport os.path\n"
        modules = {"daytrade.__synth_m__": _pm(src, "daytrade/__synth_m__.py")}
        findings = wa.find_broken_imports(modules, set(modules.keys()))
        assert not any(f["module"] == "daytrade.__synth_m__" for f in findings)


class TestDeadArtifact:
    def test_write_with_no_read_is_flagged(self):
        src = textwrap.dedent("""
            from pathlib import Path
            ROOT = Path('.')
            OUT = ROOT / "data" / "__synth__" / "report.json"

            def write():
                OUT.write_text("{}")
        """)
        modules = {"scripts.__synth_n__": _pm(src, "scripts/__synth_n__.py")}
        findings = wa.find_dead_artifacts(modules, orphan_modules=set())
        assert any(f["module"] == "data/__synth__/report.json" for f in findings)

    def test_write_with_production_read_is_not_flagged(self):
        write_src = textwrap.dedent("""
            from pathlib import Path
            ROOT = Path('.')
            OUT = ROOT / "data" / "__synth2__" / "report.json"

            def write():
                OUT.write_text("{}")
        """)
        read_src = textwrap.dedent("""
            from pathlib import Path
            ROOT = Path('.')
            OUT = ROOT / "data" / "__synth2__" / "report.json"

            def read():
                return OUT.read_text()
        """)
        modules = {
            "scripts.__synth_o__": _pm(write_src, "scripts/__synth_o__.py"),
            "scripts.__synth_p__": _pm(read_src, "scripts/__synth_p__.py"),
        }
        findings = wa.find_dead_artifacts(modules, orphan_modules=set())
        assert not any(f["module"] == "data/__synth2__/report.json" for f in findings)

    def test_write_with_only_orphaned_reader_is_still_flagged(self):
        write_src = textwrap.dedent("""
            from pathlib import Path
            ROOT = Path('.')
            OUT = ROOT / "data" / "__synth3__" / "report.json"

            def write():
                OUT.write_text("{}")
        """)
        read_src = textwrap.dedent("""
            from pathlib import Path
            ROOT = Path('.')
            OUT = ROOT / "data" / "__synth3__" / "report.json"

            def read():
                return OUT.read_text()
        """)
        modules = {
            "scripts.__synth_q__": _pm(write_src, "scripts/__synth_q__.py"),
            "scripts.__synth_r__": _pm(read_src, "scripts/__synth_r__.py"),
        }
        findings = wa.find_dead_artifacts(modules, orphan_modules={"scripts.__synth_r__"})
        assert any(f["module"] == "data/__synth3__/report.json" for f in findings)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
