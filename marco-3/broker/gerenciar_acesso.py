"""Revoga ou rotaciona UMA conta sem regenerar a CA nem as outras identidades.

A extensão recarrega o XML para novas conexões. Para invalidar imediatamente
sessões existentes, reinicie o broker após executar este comando (ver README).
O arquivo é substituído atomicamente para não carregar XML pela metade.
"""
import argparse
import os
import secrets
import xml.etree.ElementTree as ET

from preparar import CONTAS, PASTA, escrever_xml, hash_senha


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("usuario", choices=list(CONTAS))
    modo = ap.add_mutually_exclusive_group(required=True)
    modo.add_argument("--revogar", action="store_true")
    modo.add_argument("--rotacionar", action="store_true")
    args = ap.parse_args()
    local = PASTA / "local"
    arquivo = local / "extensions/hivemq-file-rbac-extension/conf/credentials.xml"
    raiz = ET.parse(arquivo).getroot()
    users = raiz.find("users")
    existente = next((u for u in users if u.findtext("name") == args.usuario), None)
    if existente is not None:
        users.remove(existente)
    if args.rotacionar:
        senha = secrets.token_urlsafe(32)
        user = ET.SubElement(users, "user")
        ET.SubElement(user, "name").text = args.usuario
        ET.SubElement(user, "password").text = hash_senha(senha)
        ET.SubElement(ET.SubElement(user, "roles"), "id").text = CONTAS[args.usuario]
        perfil = local / f"{args.usuario}.env"
        linhas = perfil.read_text(encoding="utf-8").splitlines()
        perfil.write_text("\n".join(f"MQTT_SENHA={senha}" if l.startswith("MQTT_SENHA=") else l
                                    for l in linhas) + "\n", encoding="utf-8")
        if args.usuario == "mic-esp32-01":
            header = local / "segredos.h"
            linhas = header.read_text(encoding="utf-8").splitlines()
            header.write_text("\n".join(f'#define MQTT_SENHA "{senha}"' if l.startswith("#define MQTT_SENHA ") else l
                                        for l in linhas) + "\n", encoding="utf-8")
    temporario = arquivo.with_suffix(".tmp")
    escrever_xml(temporario, raiz)
    os.replace(temporario, arquivo)
    print(f"Conta {args.usuario}: {'revogada' if args.revogar else 'rotacionada'}.")
    print("Reinicie o broker para encerrar sessões existentes; atualize somente o cliente afetado.")


if __name__ == "__main__":
    main()
