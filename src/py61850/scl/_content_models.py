# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Guilherme Marini
#
# This file is part of py61850. It is free software under the GNU Affero
# General Public License v3 or later; see LICENSE. A commercial licence,
# for use in software you do not wish to release under the AGPL, is
# available from the copyright holder -- see COMMERCIAL.md.
"""The SCL content models: which children an element may hold, in which order.

**Generated. Do not edit by hand** -- run ``tools/build_content_models.py``,
which is also what ``tests/unit/test_scl_ordering.py`` uses to check that this
file still says what the schema says.

Extracted from the SCL schema published by IEC as a code component:

    IEC 61850-6:2009/AMD1:2018 -- SCL schema version 2007B4
    IEC_61850-6.2018.SCL.2007B4.full.zip

The schema itself is not part of this repository. What is here is one
structural dimension of it -- element names and the order they go in -- and
none of its attributes, types, enumerations, cardinalities, constraints or
documentation.

**This code was derived from IEC 61850-6:2009/AMD1:2018 within modifications
permitted in the relevant IEC standard. Please reproduce this note if
possible.** The IEC copyright notice, the code-component licence conditions
and the disclaimer they require are in ``NOTICE-IEC.txt``, which ships with
every sdist and wheel. py61850 is not endorsed by or affiliated with IEC.

Each entry is a tuple of SLOTS. A slot holds one name where the schema fixes
its position and several where it does not: `DOI` below carries `SDI` and
`DAI` in one slot because `tDOI`'s content is a repeated ``xs:choice``, and
`Services` carries all 33 of its children in one because ``tServices`` is an
``xs:all``. Elements in the same slot are interchangeable; a slot always
comes before the one after it.

``##other`` is ``xs:any namespace="##other"`` -- foreign-namespace content,
which `tBaseElement` places ahead of `Text` and `Private`.
"""

CONTENT_MODELS = {
    "AccessControl": (("##other",),),
    "AccessPoint": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Server", "LN", "ServerAt"),
        ("Services",),
        ("GOOSESecurity",),
        ("SMVSecurity",),
    ),
    "Address": (("P",),),
    "BDA": (("##other",), ("Text",), ("Private",), ("Val",),),
    "Bay": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("PowerTransformer",),
        ("GeneralEquipment",),
        ("ConductingEquipment",),
        ("ConnectivityNode",),
        ("Function",),
    ),
    "ClientServices": (("TimeSyncProt",), ("McSecurity",),),
    "Communication": (("##other",), ("Text",), ("Private",), ("SubNetwork",),),
    "ConductingEquipment": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("Terminal",),
        ("SubEquipment",),
        ("EqFunction",),
    ),
    "ConnectedAP": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Address",),
        ("GSE",),
        ("SMV",),
        ("PhysConn",),
    ),
    "ConnectivityNode": (("##other",), ("Text",), ("Private",), ("LNode",),),
    "DA": (("##other",), ("Text",), ("Private",), ("Val",), ("ProtNs",),),
    "DAI": (("##other",), ("Text",), ("Private",), ("Val",),),
    "DAType": (("##other",), ("Text",), ("Private",), ("BDA",), ("ProtNs",),),
    "DO": (("##other",), ("Text",), ("Private",),),
    "DOI": (("##other",), ("Text",), ("Private",), ("SDI", "DAI"),),
    "DOType": (("##other",), ("Text",), ("Private",), ("SDO", "DA"),),
    "DataSet": (("##other",), ("Text",), ("Private",), ("FCDA",),),
    "DataTypeTemplates": (
        ("LNodeType",),
        ("DOType",),
        ("DAType",),
        ("EnumType",),
    ),
    "EnumType": (("##other",), ("Text",), ("Private",), ("EnumVal",),),
    "EqFunction": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("GeneralEquipment",),
        ("EqSubFunction",),
    ),
    "EqSubFunction": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("GeneralEquipment",),
        ("EqSubFunction",),
    ),
    "Function": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("SubFunction",),
        ("GeneralEquipment",),
        ("ConductingEquipment",),
    ),
    "GOOSESecurity": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Subject",),
        ("IssuerName",),
    ),
    "GSE": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Address",),
        ("MinTime",),
        ("MaxTime",),
    ),
    "GSEControl": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("IEDName",),
        ("Protocol",),
    ),
    "GSESettings": (("McSecurity",),),
    "GeneralEquipment": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("EqFunction",),
    ),
    "Header": (("Text",), ("History",),),
    "History": (("Hitem",),),
    "Hitem": (("##other",),),
    "IED": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Services",),
        ("AccessPoint",),
        ("KDC",),
    ),
    "Inputs": (("##other",), ("Text",), ("Private",), ("ExtRef",),),
    "LDevice": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LN0",),
        ("LN",),
        ("AccessControl",),
    ),
    "LN": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("DataSet",),
        ("ReportControl",),
        ("LogControl",),
        ("DOI",),
        ("Inputs",),
        ("Log",),
    ),
    "LN0": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("DataSet",),
        ("ReportControl",),
        ("LogControl",),
        ("DOI",),
        ("Inputs",),
        ("Log",),
        ("GSEControl",),
        ("SampledValueControl",),
        ("SettingControl",),
    ),
    "LNode": (("##other",), ("Text",), ("Private",),),
    "LNodeType": (("##other",), ("Text",), ("Private",), ("DO",),),
    "Line": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("GeneralEquipment",),
        ("Function",),
        ("Voltage",),
        ("ConductingEquipment",),
        ("ConnectivityNode",),
    ),
    "Log": (("##other",), ("Text",), ("Private",),),
    "LogControl": (("##other",), ("Text",), ("Private",), ("TrgOps",),),
    "NeutralPoint": (("##other",), ("Text",), ("Private",),),
    "PhysConn": (("##other",), ("Text",), ("Private",), ("P",),),
    "PowerTransformer": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("TransformerWinding",),
        ("SubEquipment",),
        ("EqFunction",),
    ),
    "Private": (("##other",),),
    "Process": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("GeneralEquipment",),
        ("Function",),
        ("ConductingEquipment",),
        ("Substation",),
        ("Line",),
        ("Process",),
    ),
    "ReportControl": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("TrgOps",),
        ("OptFields",),
        ("RptEnabled",),
    ),
    "RptEnabled": (("##other",), ("Text",), ("Private",), ("ClientLN",),),
    "SCL": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Header",),
        ("Substation",),
        ("Communication",),
        ("IED",),
        ("DataTypeTemplates",),
        ("Line",),
        ("Process",),
    ),
    "SDI": (("##other",), ("Text",), ("Private",), ("SDI", "DAI"),),
    "SDO": (("##other",), ("Text",), ("Private",),),
    "SMV": (("##other",), ("Text",), ("Private",), ("Address",),),
    "SMVSecurity": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Subject",),
        ("IssuerName",),
    ),
    "SMVSettings": (
        ("SmpRate", "SamplesPerSec", "SecPerSamples"),
        ("McSecurity",),
    ),
    "SampledValueControl": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("IEDName",),
        ("SmvOpts",),
        ("Protocol",),
    ),
    "Server": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("Authentication",),
        ("LDevice",),
        ("Association",),
    ),
    "ServerAt": (("##other",), ("Text",), ("Private",),),
    "Services": (
        ("DynAssociation", "SettingGroups", "GetDirectory", "GetDataObjectDefinition", "DataObjectDirectory", "GetDataSetValue", "SetDataSetValue", "DataSetDirectory", "ConfDataSet", "DynDataSet", "ReadWrite", "TimerActivatedControl", "ConfReportControl", "GetCBValues", "ConfLogControl", "ReportSettings", "LogSettings", "GSESettings", "SMVSettings", "GSEDir", "GOOSE", "GSSE", "SMVsc", "FileHandling", "ConfLNs", "ClientServices", "ConfLdName", "SupSubscription", "ConfSigRef", "ValueHandling", "RedProt", "TimeSyncProt", "CommProt"),
    ),
    "SettingControl": (("##other",), ("Text",), ("Private",),),
    "SettingGroups": (("SGEdit", "ConfSG"),),
    "SubEquipment": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("EqFunction",),
    ),
    "SubFunction": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("GeneralEquipment",),
        ("ConductingEquipment",),
        ("SubFunction",),
    ),
    "SubNetwork": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("BitRate",),
        ("ConnectedAP",),
    ),
    "Substation": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("PowerTransformer",),
        ("GeneralEquipment",),
        ("VoltageLevel",),
        ("Function",),
    ),
    "TapChanger": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("SubEquipment",),
        ("EqFunction",),
    ),
    "Terminal": (("##other",), ("Text",), ("Private",),),
    "Text": (("##other",),),
    "TransformerWinding": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("Terminal",),
        ("SubEquipment",),
        ("TapChanger",),
        ("NeutralPoint",),
        ("EqFunction",),
    ),
    "VoltageLevel": (
        ("##other",),
        ("Text",),
        ("Private",),
        ("LNode",),
        ("PowerTransformer",),
        ("GeneralEquipment",),
        ("Voltage",),
        ("Bay",),
        ("Function",),
    ),
}
