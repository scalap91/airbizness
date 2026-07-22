"""Scorecard Ratehawk — paramètres pour le scoring multi-critères.

⚠️ Squelette : Ratehawk n'est PAS encore allumé chez AirBizness. Les valeurs
sont des hypothèses raisonnables à ajuster quand on signera le contrat.

Lu par providers/base/scoring.py si Ratehawk est activé dans feature_flags.
"""

MARGIN_PCT          = 0.18                            # hypothèse : ils paient plus que HBX (provider plus jeune, marge plus haute pour gagner partsmarchés)
PAYMENT_DELAY_DAYS  = 60                              # hypothèse : paiement à 60j (à confirmer)
SPECIALTY_REGIONS   = ["RU", "BY", "KZ", "GE", "AM", "TH", "VN"]  # zones où Ratehawk est historiquement plus fort
SLA_RELIABILITY_TGT = 0.92                            # plus jeune, moins éprouvé que HBX
