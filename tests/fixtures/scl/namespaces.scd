<?xml version="1.0" encoding="UTF-8"?>
<SCL xmlns="http://www.iec.ch/61850/2003/SCL" xmlns:sxy="http://www.iec.ch/61850/2003/SCLcoordinates" xmlns:sel="http://www.selinc.com/2006/61850" version="2007" revision="B" release="4">
  <Header id="NAMESPACE FIXTURE" version="1" revision="1" toolID="hand-written"/>
  <Substation name="S1" desc="the smallest thing that can carry a coordinate" sxy:x="1" sxy:y="1">
    <VoltageLevel name="V1" sxy:x="2" sxy:y="1">
      <Voltage unit="V" multiplier="k">138</Voltage>
      <Bay name="B1" sxy:x="3" sxy:y="2">
        <ConductingEquipment name="Q1" type="CBR" sxy:x="3" sxy:y="4"/>
      </Bay>
    </VoltageLevel>
  </Substation>
</SCL>
