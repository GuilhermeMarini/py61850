"""Directory and data-definition services.

    GetServerDirectory        -> GetNameList(class=domain, scope=vmd)
    GetLogicalDeviceDirectory -> GetNameList(class=namedVariable, scope=domain)
    (data sets)               -> GetNameList(class=namedVariableList, scope=domain)
    GetDataDefinition         -> GetVariableAccessAttributes

MMS has no "list the logical nodes" service: the IEC 61850-8-1 mapping flattens
the whole data model of a logical device into one namedVariable list, where an
LN is the first ``$``-separated component (``ACN1GGIO1$ST$Ind1$stVal``) and,
on most servers, a bare entry of its own.  :meth:`DirectoryMixin.get_logical_nodes`
is that list reduced back to the LN view ACSI GetLogicalDeviceDirectory
describes -- one GetNameList, no extra requests.
"""

import fnmatch
import re

from .. import pdu


def parse_ln_name(name):
    """Split an LN name into ``(prefix, ln_class, instance)``.

    IEC 61850-7-2 names an LN ``prefix + class + instance``, the class always
    four characters: ``'ACN1GGIO1' -> ('ACN1', 'GGIO', '1')``,
    ``'DevIDLPHD1' -> ('DevID', 'LPHD', '1')``. LLN0 is the one LN with no
    instance number.
    """
    if name == "LLN0":
        return "", "LLN0", ""
    end = len(name)
    while end > 0 and name[end - 1].isdigit():
        end -= 1
    head, instance = name[:end], name[end:]
    if len(head) >= 4:
        return head[:-4], head[-4:], instance
    return "", head, instance


class LogicalNode:
    """One logical node of a logical device: ``MYLD_ANN/ACN1GGIO1``."""

    __slots__ = ("ld", "name", "prefix", "ln_class", "instance")

    def __init__(self, ld, name):
        self.ld = ld
        self.name = name
        self.prefix, self.ln_class, self.instance = parse_ln_name(name)

    @property
    def ref(self):
        """The IEC 61850 object reference, ``LDName/LNName``."""
        return f"{self.ld}/{self.name}"

    def __eq__(self, other):
        return (isinstance(other, LogicalNode)
                and (self.ld, self.name) == (other.ld, other.name))

    def __hash__(self):
        return hash((self.ld, self.name))

    def __repr__(self):
        return f"{self.ref}  ({self.ln_class})"


def logical_nodes_from_names(ld, names):
    """Reduce one LD's namedVariable list to its logical nodes, in server order.

    Works off the first component of every name, so it is right both for servers
    that list the LN as a bare entry and for those that only list leaves.
    """
    seen, out = set(), []
    for n in names:
        head = n.split("$", 1)[0]
        if head and head not in seen:
            seen.add(head)
            out.append(LogicalNode(ld, head))
    return out


def compile_ln_filter(ln_class=None, pattern=None, regex=None, ignore_case=True):
    """Build ``predicate(LogicalNode) -> bool``.

    ``ln_class``  one class or a list: ``'MMXU'``, ``['PTOC', 'PTRC']``.
    ``pattern``   glob on the LN name, e.g. ``'ACN*GGIO*'``.
    ``regex``     regex searched in the ``LD/LN`` reference.

    Values inside one kind are OR-ed, the kinds are AND-ed -- the same rule the
    file search uses. No filter at all matches every LN.
    """
    def fold(s):
        return s.lower() if ignore_case else s

    tests = []

    if ln_class:
        classes = [ln_class] if isinstance(ln_class, str) else list(ln_class)
        classes = [fold(c) for c in classes if c]
        if classes:
            tests.append(lambda ln, cs=classes: fold(ln.ln_class) in cs)

    if pattern:
        globs = [pattern] if isinstance(pattern, str) else list(pattern)
        globs = [fold(g) for g in globs if g]
        if globs:
            tests.append(lambda ln, gs=globs: any(
                fnmatch.fnmatchcase(fold(ln.name), g) for g in gs))

    if regex:
        rx = regex if hasattr(regex, "search") else re.compile(
            regex, re.IGNORECASE if ignore_case else 0)
        tests.append(lambda ln, rx=rx: rx.search(ln.ref) is not None)

    if not tests:
        return lambda ln: True
    return lambda ln: all(t(ln) for t in tests)


class DirectoryMixin:
    def get_name_list(self, object_class, scope, domain=None):
        """Return the full identifier list, following moreFollows continuations."""
        names, cont = [], None
        while True:
            resp = self._transact(
                pdu.build_get_name_list(object_class, scope, domain, cont))
            batch, more = pdu.decode_name_list(resp)
            names.extend(batch)
            if not more or not names:
                break
            cont = names[-1]
        return names

    def get_server_directory(self):
        return self.get_name_list(pdu.CLASS_DOMAIN, "vmd")

    def get_logical_device_directory(self, ld):
        return self.get_name_list(pdu.CLASS_NAMED_VARIABLE, "domain", ld)

    def get_data_set_directory(self, ld):
        return self.get_name_list(pdu.CLASS_NAMED_VARIABLE_LIST, "domain", ld)

    # ---- Logical nodes ---------------------------------------------------
    def get_logical_nodes(self, ld, ln_class=None, pattern=None, regex=None,
                          ignore_case=True):
        """The logical nodes of one logical device, as :class:`LogicalNode`.

        The ACSI GetLogicalDeviceDirectory view of what
        :meth:`get_logical_device_directory` returns flat: one GetNameList,
        reduced to its LN level, in the order the server reports.

            c.get_logical_nodes("MYLD_PROT")
            c.get_logical_nodes(ld, ln_class=["PTOC", "PTRC"])
            c.get_logical_nodes(ld, pattern="ACN*")

        See :func:`compile_ln_filter` for how the filters combine.
        """
        matches = compile_ln_filter(ln_class, pattern, regex, ignore_case)
        nodes = logical_nodes_from_names(ld, self.get_logical_device_directory(ld))
        return [ln for ln in nodes if matches(ln)]

    def find_logical_nodes(self, ln_class=None, ld=None, pattern=None, regex=None,
                           ignore_case=True):
        r"""Find logical nodes across the whole server -- "where are the MMXUs?".

        `ld` limits the search to one logical device or a list of them;
        by default every LD the server reports is scanned, which costs one
        GetNameList per LD. Each hit carries the LD it was found in
        (``ln.ld``, ``ln.ref``).

            c.find_logical_nodes("MMXU")
            c.find_logical_nodes(["PTOC", "PTRC"], ld="MYLD_PROT")
            c.find_logical_nodes(regex=r"GGIO\d+$")
        """
        if ld is None:
            lds = self.get_server_directory()
        else:
            lds = [ld] if isinstance(ld, str) else list(ld)
        out = []
        for name in lds:
            out.extend(self.get_logical_nodes(name, ln_class, pattern, regex,
                                              ignore_case))
        return out

    def get_data_definition(self, ld, item):
        """GetVariableAccessAttributes -> raw typeDescription TLV bytes."""
        return self._transact(pdu.build_get_var_access_attributes(ld, item))
