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

    def test_field_supplied_only_positionally_is_not_flagged(self):
        """THE REAL HISTORICAL CASE. daytrade/stockfish_constitution.py at
        commit 083e09e defined `ConstitutionViolation` with 5 fields and had
        10 production call sites passing all 5 as POSITIONAL arguments — zero
        as `action_kind=`. find_no_supplier() only recognised keyword args
        and attribute assignments as "supplying" a field, so it reported
        ConstitutionViolation.action_kind as NO_SUPPLIER even though it was
        supplied every single time. That false finding was acted on before
        this fix (and this test) existed. A dataclass with 5 fields,
        constructed with 5 positional args and no keywords, must NOT be
        reported as NO_SUPPLIER for its last field."""
        src = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class ConstitutionViolation:
                rule_id: str
                rule: str
                detail: str
                state_revision: int
                action_kind: Optional[str] = None

            def validate(k):
                return ConstitutionViolation("C009", "some rule", "detail text", 3, k)

            def read_it(v):
                return v.action_kind
        """
        modules = {"daytrade.__synth_positional__": _pm(src, "daytrade/__synth_positional__.py")}
        findings = wa.find_no_supplier(modules)
        assert not any(f["field"] == "ConstitutionViolation.action_kind" for f in findings)

    def test_field_supplied_only_via_starred_call_is_not_flagged_but_not_treated_as_wired_either(self):
        """*args splats make positional resolution impossible — the detector
        must not guess. It must not crash, and (per the "false negative is
        worse" rule) it must not silently mark the field wired off an
        unprovable splat: a genuinely unsupplied field behind a splat-only
        call site should stay flagged."""
        src = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Thing:
                a: str
                b: str
                x: Optional[float] = None

            def make(args):
                return Thing(*args)

            def read_it(t):
                return t.x
        """
        modules = {"daytrade.__synth_starred__": _pm(src, "daytrade/__synth_starred__.py")}
        findings = wa.find_no_supplier(modules)
        assert any(f["field"] == "Thing.x" for f in findings)

    def test_positional_supply_does_not_leak_across_same_named_classes(self):
        """Two unrelated dataclasses sharing a field name (and, here, even a
        class name in different modules) must not cross-contaminate: a
        positional/keyword supply to ONE class's field must not be read as
        evidence that an unrelated class's same-named field is supplied. This
        guards the exact regression the (defining_module, class, field)
        scoping in find_no_supplier() fixes."""
        src_a = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Holder:
                intent: Optional[str] = None

            def make():
                return Holder("wired")

            def read_a(h):
                return h.intent
        """
        src_b = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Holder:
                kind: str
                order_id: str
                intent: Optional[str] = None

            def read_b(h):
                return h.intent
        """
        modules = {
            "daytrade.__synth_holder_a__": _pm(src_a, "daytrade/__synth_holder_a__.py"),
            "daytrade.__synth_holder_b__": _pm(src_b, "daytrade/__synth_holder_b__.py"),
        }
        findings = wa.find_no_supplier(modules)
        # Holder.intent in module B is genuinely never supplied and must still
        # be flagged, even though module A's same-named class/field IS
        # supplied.
        assert any(
            f["module"] == "daytrade.__synth_holder_b__" and f["field"] == "Holder.intent"
            for f in findings
        )
        assert not any(
            f["module"] == "daytrade.__synth_holder_a__" and f["field"] == "Holder.intent"
            for f in findings
        )

    def test_field_supplied_via_setattr_is_not_flagged(self):
        src = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Thing:
                x: Optional[float] = None

            def read_it(t):
                return t.x

            def make(t):
                setattr(t, "x", 1.0)
        """
        modules = {"daytrade.__synth_setattr__": _pm(src, "daytrade/__synth_setattr__.py")}
        findings = wa.find_no_supplier(modules)
        assert not any(f["field"] == "Thing.x" for f in findings)

    def test_field_never_reached_by_short_positional_call_is_still_flagged(self):
        """A genuinely unsupplied field must still be caught: a positional
        call that doesn't reach far enough to cover the target field's index
        is not a supply. Guards against over-correcting the positional fix
        into blindness the other way."""
        src = """
            from dataclasses import dataclass
            from typing import Optional

            @dataclass
            class Thing:
                a: str
                b: str
                x: Optional[float] = None

            def make():
                return Thing("a", "b")

            def read_it(t):
                return t.x
        """
        modules = {"daytrade.__synth_short_call__": _pm(src, "daytrade/__synth_short_call__.py")}
        findings = wa.find_no_supplier(modules)
        assert any(f["field"] == "Thing.x" for f in findings)


class TestImportedNotCalledDecoratorBlindspot:
    def test_bare_decorator_use_of_imported_symbol_is_not_flagged(self):
        """A parameterized decorator (`@foo(...)`) is an ast.Call and was
        already handled. A bare decorator (`@foo`, no parens) is NOT a Call
        node in the AST — Python's grammar applies it directly — so the
        original `_names_used_as_call_func` never saw it as a "use",
        risking a false-positive IMPORTED_NOT_CALLED for modules that only
        ever use an imported symbol as a bare decorator."""
        modules = {
            "daytrade.__synth_dec_a__": _pm("def deco(f):\n    return f\n", "daytrade/__synth_dec_a__.py"),
            "daytrade.__synth_dec_b__": _pm(
                "from __synth_dec_a__ import deco\n\n@deco\ndef handler():\n    pass\n",
                "daytrade/__synth_dec_b__.py",
            ),
        }
        findings = wa.find_imported_not_called(modules, set(modules.keys()))
        assert not any(f["module"] == "daytrade.__synth_dec_b__" for f in findings)


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
