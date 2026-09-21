"""
cdisc_export.py — Exports CDISC complémentaires : domaine DM
(Demographics) et un define.xml de départ.

Important, à indiquer clairement à votre data manager / votre CRO :
le define.xml généré ici couvre uniquement les domaines LB et DM tels
qu'implémentés dans cette application, avec une structure Define-XML
v2.0 simplifiée. Avant tout dépôt réglementaire réel, il doit être
complété (Comments, MethodDefs, CodeLists complets, VariableOrigin
précis par variable) et validé avec un outil de contrôle CDISC
(Pinnacle 21 ou équivalent). Ce fichier est un point de départ, pas un
define.xml certifié.
"""
from datetime import date

import pandas as pd

from db import read_full_results
from constants import STUDYID


def generate_dm_domain(conn) -> pd.DataFrame:
    """Domaine DM (Demographics) — une ligne par patient."""
    df = read_full_results(conn)
    if df.empty:
        return pd.DataFrame(columns=["STUDYID", "DOMAIN", "USUBJID", "SUBJID", "SITEID",
                                      "AGE", "AGEU", "SEX", "COUNTRY", "RFSTDTC"])

    patients = df.drop_duplicates(subset=["usubjid"]).copy()
    current_year = date.today().year
    patients["AGE"] = current_year - patients["birth_year"]

    # RFSTDTC (date de référence de début) : variable DM attendue par CDISC,
    # calculée ici comme la date de la première visite documentée du patient
    # (généralement VINC) — pas encore présente avant cet ajout.
    first_visit_date = (
        df.sort_values("visit_date").drop_duplicates(subset=["usubjid"], keep="first")
        .set_index("usubjid")["visit_date"]
    )
    patients["RFSTDTC"] = patients["usubjid"].map(first_visit_date)

    dm = pd.DataFrame({
        "STUDYID": STUDYID,
        "DOMAIN": "DM",
        "USUBJID": patients["usubjid"],
        "SUBJID": patients["patient_id"],
        "SITEID": patients["site_id"],
        "AGE": patients["AGE"],
        "AGEU": "YEARS",
        "SEX": patients["sex"],
        "COUNTRY": patients["country"],
        "RFSTDTC": patients["RFSTDTC"],
    })
    return dm.reset_index(drop=True)


_DEFINE_XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!-- Define-XML v2.0 (starter / non certifie) - genere par l'application BLOOD Study LIMS -->
<ODM xmlns="http://www.cdisc.org/ns/odm/v1.3" xmlns:def="http://www.cdisc.org/ns/def/v2.0"
     FileType="Snapshot" FileOID="define.blood.{generated_date}" CreationDateTime="{generated_datetime}"
     ODMVersion="1.3.2">
  <Study OID="{studyid}">
    <GlobalVariables>
      <StudyName>{studyid} - Blood Study</StudyName>
      <StudyDescription>Diabetes clinical trial - blood test results (LB) and demographics (DM)</StudyDescription>
      <ProtocolName>{studyid}</ProtocolName>
    </GlobalVariables>
    <MetaDataVersion OID="MDV.1" Name="{studyid} SDTM Define (LB, DM)" def:DefineVersion="2.0.0">

      <ItemGroupDef OID="IG.LB" Name="LB" Repeating="Yes" Domain="LB" def:Structure="One record per lab test per visit per subject" def:Class="FINDINGS">
        <Description><TranslatedText xml:lang="en">Laboratory Test Results</TranslatedText></Description>
        <ItemRef ItemOID="IT.LB.STUDYID" OrderNumber="1" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.LB.DOMAIN" OrderNumber="2" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.LB.USUBJID" OrderNumber="3" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.LB.LBSEQ" OrderNumber="4" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.LB.LBTESTCD" OrderNumber="5" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.LB.LBTEST" OrderNumber="6" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.LB.LBORRES" OrderNumber="7" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.LBORRESU" OrderNumber="8" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.LBSTRESN" OrderNumber="9" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.LBSTRESU" OrderNumber="10" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.VISITNUM" OrderNumber="11" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.VISIT" OrderNumber="12" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.LBDTC" OrderNumber="13" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.LBNRIND" OrderNumber="14" Mandatory="No"/>
        <ItemRef ItemOID="IT.LB.LBBLFL" OrderNumber="15" Mandatory="No"/>
      </ItemGroupDef>

      <ItemGroupDef OID="IG.DM" Name="DM" Repeating="No" Domain="DM" def:Structure="One record per subject" def:Class="SPECIAL PURPOSE">
        <Description><TranslatedText xml:lang="en">Demographics</TranslatedText></Description>
        <ItemRef ItemOID="IT.DM.STUDYID" OrderNumber="1" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.DM.DOMAIN" OrderNumber="2" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.DM.USUBJID" OrderNumber="3" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.DM.SUBJID" OrderNumber="4" Mandatory="Yes"/>
        <ItemRef ItemOID="IT.DM.SITEID" OrderNumber="5" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.AGE" OrderNumber="6" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.AGEU" OrderNumber="7" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.SEX" OrderNumber="8" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.COUNTRY" OrderNumber="9" Mandatory="No"/>
        <ItemRef ItemOID="IT.DM.RFSTDTC" OrderNumber="10" Mandatory="No"/>
      </ItemGroupDef>

      <!-- ItemDefs simplifies : DataType/Length indicatifs, a affiner avant depot reglementaire -->
      <ItemDef OID="IT.LB.STUDYID" Name="STUDYID" DataType="text" Length="20"/>
      <ItemDef OID="IT.LB.DOMAIN" Name="DOMAIN" DataType="text" Length="2"/>
      <ItemDef OID="IT.LB.USUBJID" Name="USUBJID" DataType="text" Length="40"/>
      <ItemDef OID="IT.LB.LBSEQ" Name="LBSEQ" DataType="integer" Length="8"/>
      <ItemDef OID="IT.LB.LBTESTCD" Name="LBTESTCD" DataType="text" Length="8"/>
      <ItemDef OID="IT.LB.LBTEST" Name="LBTEST" DataType="text" Length="40"/>
      <ItemDef OID="IT.LB.LBORRES" Name="LBORRES" DataType="text" Length="40"/>
      <ItemDef OID="IT.LB.LBORRESU" Name="LBORRESU" DataType="text" Length="20"/>
      <ItemDef OID="IT.LB.LBSTRESN" Name="LBSTRESN" DataType="float" Length="8"/>
      <ItemDef OID="IT.LB.LBSTRESU" Name="LBSTRESU" DataType="text" Length="20"/>
      <ItemDef OID="IT.LB.VISITNUM" Name="VISITNUM" DataType="integer" Length="8"/>
      <ItemDef OID="IT.LB.VISIT" Name="VISIT" DataType="text" Length="20"/>
      <ItemDef OID="IT.LB.LBDTC" Name="LBDTC" DataType="text" Length="20"/>
      <ItemDef OID="IT.LB.LBNRIND" Name="LBNRIND" DataType="text" Length="8"/>
      <ItemDef OID="IT.LB.LBBLFL" Name="LBBLFL" DataType="text" Length="1"/>

      <ItemDef OID="IT.DM.STUDYID" Name="STUDYID" DataType="text" Length="20"/>
      <ItemDef OID="IT.DM.DOMAIN" Name="DOMAIN" DataType="text" Length="2"/>
      <ItemDef OID="IT.DM.USUBJID" Name="USUBJID" DataType="text" Length="40"/>
      <ItemDef OID="IT.DM.SUBJID" Name="SUBJID" DataType="text" Length="20"/>
      <ItemDef OID="IT.DM.SITEID" Name="SITEID" DataType="text" Length="20"/>
      <ItemDef OID="IT.DM.AGE" Name="AGE" DataType="integer" Length="3"/>
      <ItemDef OID="IT.DM.AGEU" Name="AGEU" DataType="text" Length="10"/>
      <ItemDef OID="IT.DM.SEX" Name="SEX" DataType="text" Length="1"/>
      <ItemDef OID="IT.DM.COUNTRY" Name="COUNTRY" DataType="text" Length="3"/>
      <ItemDef OID="IT.DM.RFSTDTC" Name="RFSTDTC" DataType="text" Length="20"/>

    </MetaDataVersion>
  </Study>
</ODM>
"""


def generate_define_xml() -> bytes:
    now = pd.Timestamp.utcnow()
    xml = _DEFINE_XML_TEMPLATE.format(
        studyid=STUDYID,
        generated_date=now.strftime("%Y%m%d"),
        generated_datetime=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return xml.encode("utf-8")
