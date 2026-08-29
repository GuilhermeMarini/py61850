"""SCL -- the substation configuration files (.scd / .cid / .icd).

**Planned; nothing implemented yet.** See ROADMAP 1.0.

    model   IEDs, LDs, LNs, DOs, DAs, DataSets, Report/Setting control blocks
            as an in-memory object model
    parse   XML -> model

This is the shared spine of both simulators: the MMS server answers
GetNameList/GetVariableAccessAttributes/Read out of this model, and the GOOSE
publisher builds its frames from the GoCB and dataset in the same model.  It is
pure XML parsing over the standard library's ``xml.etree`` -- no network, no
privileges.
"""

__all__ = []
