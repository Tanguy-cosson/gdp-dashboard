"""
hl7_import.py — Import de résultats via message HL7 v2.x (ORU^R01).

CE QUE CE MODULE FAIT : parser un message HL7 déjà reçu (fichier
.hl7/.txt).

CE QUE CE MODULE NE FAIT PAS : écouter un port réseau ou série en
temps réel (MLLP/RS-232) pour recevoir les messages automatiquement
depuis l'automate. Cette écoute doit tourner en permanence sur une
machine du réseau du laboratoire — ce n'est pas compatible avec une
application Streamlit (Cloud ou non). Un vrai lien temps réel
nécessite un petit service dédié hébergé dans le réseau du labo.
"""
from dataclasses import dataclass, field


@dataclass
class HL7Observation:
    test_code: str
    test_name: str
    value: str
    unit: str
    ref_range: str
    abnormal_flag: str


@dataclass
class HL7ParsedMessage:
    patient_identifier: str = ""
    patient_sex: str = ""
    patient_birth_year: int = None
    message_datetime: str = ""
    observations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _split_fields(segment_line, sep="|"):
    return segment_line.split(sep)


def parse_oru_r01(raw_text: str) -> HL7ParsedMessage:
    """Parse un message HL7 v2 ORU^R01. Tolérant : les segments ou
    champs absents/mal formés génèrent un avertissement dans
    `warnings` plutôt qu'une exception."""
    result = HL7ParsedMessage()
    lines = [l.strip() for l in raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if l.strip()]

    if not lines:
        result.warnings.append("Fichier vide.")
        return result

    found_msh = False
    for line in lines:
        segment_id = line[:3]

        if segment_id == "MSH":
            found_msh = True
            fields = _split_fields(line)
            if len(fields) > 6:
                result.message_datetime = fields[6]
            msg_type = fields[8] if len(fields) > 8 else ""
            if "ORU" not in msg_type and "R01" not in msg_type:
                result.warnings.append(
                    f"Type de message '{msg_type}' inattendu (ORU^R01 attendu). "
                    "Le fichier sera quand même parsé, vérifiez le résultat."
                )

        elif segment_id == "PID":
            fields = _split_fields(line)
            if len(fields) > 3 and fields[3]:
                result.patient_identifier = fields[3].split("^")[0]
            else:
                result.warnings.append("Segment PID sans identifiant patient (PID-3 vide).")
            if len(fields) > 7 and fields[7]:
                try:
                    result.patient_birth_year = int(fields[7][:4])
                except ValueError:
                    result.warnings.append(f"Date de naissance illisible : '{fields[7]}'.")
            if len(fields) > 8 and fields[8]:
                sex = fields[8].strip().upper()
                result.patient_sex = sex if sex in ("M", "F") else ""
                if result.patient_sex == "":
                    result.warnings.append(f"Sexe HL7 '{fields[8]}' non reconnu (attendu M/F).")

        elif segment_id == "OBX":
            fields = _split_fields(line)
            if len(fields) < 6:
                result.warnings.append(f"Segment OBX incomplet, ignoré : {line[:60]}...")
                continue
            code_field = fields[3] if len(fields) > 3 else ""
            code_parts = code_field.split("^")
            test_code = code_parts[0] if code_parts else ""
            test_name = code_parts[1] if len(code_parts) > 1 else test_code

            value = fields[5] if len(fields) > 5 else ""
            unit = fields[6] if len(fields) > 6 else ""
            ref_range = fields[7] if len(fields) > 7 else ""
            abnormal_flag = fields[8] if len(fields) > 8 else ""

            if not test_code or not value:
                result.warnings.append(f"OBX sans code de test ou sans valeur, ignoré : {line[:60]}...")
                continue

            result.observations.append(HL7Observation(
                test_code=test_code, test_name=test_name, value=value,
                unit=unit, ref_range=ref_range, abnormal_flag=abnormal_flag,
            ))

    if not found_msh:
        result.warnings.append("Aucun segment MSH trouvé — ce fichier ressemble-t-il vraiment à un message HL7 ?")
    if not result.patient_identifier:
        result.warnings.append("Aucun identifiant patient trouvé (segment PID manquant ou incomplet).")
    if not result.observations:
        result.warnings.append("Aucun résultat exploitable trouvé (segments OBX manquants ou incomplets).")

    return result


def parse_ref_range(ref_range: str):
    """Convertit '4.0-6.0' en (ref_low, ref_high). Renvoie (None, None)
    si le format n'est pas reconnu."""
    if not ref_range or "-" not in ref_range:
        return None, None
    try:
        low_str, high_str = ref_range.split("-", 1)
        return float(low_str.strip()), float(high_str.strip())
    except ValueError:
        return None, None
    