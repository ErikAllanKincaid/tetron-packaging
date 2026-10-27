from libspec import Constraint

# Enforced by reconcile.py. enforcement_logic reads the JSON context it prints.


class UnitTestsPass(Constraint):
    """CONSTRAINT-ID: CON-P01

    python3 -m unittest discover tests passes.
    ENFORCEMENT (reconcile.py): tests.passed is true.
    """
    constraint_id = "CON-P01"
    enforcement_logic = "{{ tests.passed }}"


class StdlibOnly(Constraint):
    """CONSTRAINT-ID: CON-P02

    mkdeb.py imports only standard library modules.
    ENFORCEMENT (reconcile.py): stdlib.violations equals 0.
    """
    constraint_id = "CON-P02"
    enforcement_logic = "{{ stdlib.violations == 0 }}"


class ScriptsParse(Constraint):
    """CONSTRAINT-ID: CON-P03

    Every shell script parses (sh -n) and mkdeb.py compiles.
    ENFORCEMENT (reconcile.py): syntax.failures equals 0.
    """
    constraint_id = "CON-P03"
    enforcement_logic = "{{ syntax.failures == 0 }}"


class NoSecretsInTree(Constraint):
    """CONSTRAINT-ID: CON-P04

    No private keys or tokens in tracked files.
    ENFORCEMENT (reconcile.py): secrets.count equals 0.
    """
    constraint_id = "CON-P04"
    enforcement_logic = "{{ secrets.count == 0 }}"


class ActionReferencesExist(Constraint):
    """CONSTRAINT-ID: CON-P05

    Every file action.yml references exists in the repository.
    ENFORCEMENT (reconcile.py): action.missing equals 0.
    """
    constraint_id = "CON-P05"
    enforcement_logic = "{{ action.missing == 0 }}"


class LocalDocsIgnored(Constraint):
    """CONSTRAINT-ID: CON-P06

    DO-NOT-COMMIT/ is gitignored and nothing under it is tracked.
    ENFORCEMENT (reconcile.py): local_docs.ignored is true and
    local_docs.tracked equals 0.
    """
    constraint_id = "CON-P06"
    enforcement_logic = "{{ local_docs.ignored and local_docs.tracked == 0 }}"
