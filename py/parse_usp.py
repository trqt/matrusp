#!/usr/bin/env python3
import asyncio
import argparse
import codecs
import gzip
import json
import logging
import os
import re
import time
import urllib.request
from collections import defaultdict
from pprint import pprint

import aiohttp
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from multi_key_dict import multi_key_dict

campus_por_unidade = multi_key_dict({
#    (86, 27, 39, 7, 22, 3, 16, 9, 2, 12, 48, 8, 5, 10, 67, 23, 6, 66, 14, 26, 93, 41, 92, 42, 4, 37, 43, 44, 45, 83, 47,
#     46, 87, 21, 31, 85, 71, 32, 38, 33): "São Paulo",
#    (98, 94, 60, 89, 81, 59, 96, 91, 17, 58, 95): "Ribeirão Preto",
#    (88,): "Lorena",
    (18, 97, 99, 55, 76, 75, 90): "São Carlos",
#    (11, 64): "Piracicaba",
#    (25, 61): "Bauru",
#    (74,): "Pirassununga",
#    (30,): "São Sebastião"
})

codigos_unidades = {}

async def main():
    t = time.perf_counter()

    logger.info(" - Obtendo a lista de todas as unidades de ensino - ")

    async with aiohttp.ClientSession() as session:
        async with session.get('https://uspdigital.usp.br/jupiterweb/jupColegiadoLista?tipo=T') as response:
            response_text = await response.text()
    soup = BeautifulSoup(response_text, "html.parser")

    links_unidades = soup.find_all('a', href=re.compile("jupColegiadoMenu"))

    global codigos_unidades
    codigos_unidades = {link.string: re.search(r"codcg=(\d+)", link.get('href')).group(1) for link in links_unidades}

    campi = defaultdict(list)
    for unidade, codigo in codigos_unidades.items():
        campus = campus_por_unidade.get(int(codigo), "Outro")
        campi[campus].append(unidade)

    campi_json = json.dumps(campi)

    with open(os.path.join(args.diretorio_destino, 'campi.json'), "w") as arq:
        arq.write(campi_json)
    if not args.nogzip:
        with gzip.open(os.path.join(args.diretorio_destino, 'campi.json.gz'), "wb") as arq:
            arq.write(campi_json.encode('utf-8'))

    logger.info(" - %d unidades de ensino encontradas - " % (len(codigos_unidades)))

    unidades_a_iterar = args.unidades if args.unidades else codigos_unidades.values()
    materias = await iterar_unidades(unidades_a_iterar)

    materias_json = json.dumps(materias)

    with open(os.path.join(args.diretorio_destino, args.out), "w") as arq:
        arq.write(materias_json)
    if not args.nogzip:
        with gzip.open(os.path.join(args.diretorio_destino, args.out + ".gz"), "wb") as arq:
            arq.write(materias_json.encode('utf-8'))

    logger.info(f" -   {len(materias)} materias salvas")

    logger.info(" - FIM! -")
    logger.info(f" - \n - Tempo de execução: {time.perf_counter() - t} segundos")
    return 0

async def iterar_unidades(codigos_unidades):
    async with aiohttp.ClientSession(headers={'User-Agent': 'MatrUSPbot/2.0 (+http://www.github.com/matrusp/matrusp)'}) as session:
        logger.info(" - Iniciando processamento de unidades")
        unidade_tasks = await asyncio.gather(*(iterar_unidade(codigo, session) for codigo in codigos_unidades))

        logger.info(f" -   {len(unidade_tasks)} unidades processadas")
        logger.info(" - Iniciando processamento de materias")

        coros = [parsear_materia(materia, session) for materias_unidade in unidade_tasks for materia in materias_unidade]

        logger.info(f" -   {len(coros)} materias encontradas")
        materias_tasks = await asyncio.gather(*coros)

        logger.info(f" -   {len([materia for materia in materias_tasks if materia])} materias processadas")
        return [materia for materia in materias_tasks if materia and materia[0] == 'S']

async def iterar_unidade(codigo, session):
    logger.debug(" -    Obtendo as materias da unidade %s - " % (codigo))
    async with session.get('https://uspdigital.usp.br/jupiterweb/jupDisciplinaLista?letra=A-Z&tipo=T&codcg=' + codigo, timeout = 120) as response:
        assert response.status == 200
        soup = BeautifulSoup(await response.text(), "html.parser")
    links_materias = soup.find_all('a', href=re.compile("obterTurma"))
    materias = list(map(extrai_materia, links_materias))
    logger.debug(f" -   {len(materias)} materias encontradas na unidade {codigo} - ")
    return materias

def eh_tabela_folha(tag):
    return tag.name == "table" and tag.table is None

async def parsear_materia(materia, session):
    if not materia:
        return

    async with semaforo:
        codigo = materia[0]
        logger.debug(f" -      Obtendo turmas de {codigo} - {materia[1]}")
        try:
            response = await session.get('https://uspdigital.usp.br/jupiterweb/obterTurma?print=true&sgldis=' + codigo, timeout=args.timeout, ssl=False)
            assert response.status == 200
            response_text = await response.text()
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f" -      Erro ao obter turmas de {codigo}: {e}. Tentando novamente...")
            try:
                response = await session.get('https://uspdigital.usp.br/jupiterweb/obterTurma?print=true&sgldis=' + codigo, timeout=args.timeout * 2, ssl=False)
                assert response.status == 200
                response_text = await response.text()
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                logger.error(f" -      Erro ao obter turmas de {codigo} após nova tentativa: {e}")
                return
        except Exception as _:
            logger.exception(f" -      Não foi possível obter turmas de {codigo} - {materia[1]}")
            return

        logger.debug(f" -      Analisando turmas de {codigo} - {materia[1]}")
        soup = BeautifulSoup(response_text, "html.parser")
        tabelas_folha = soup.find_all(eh_tabela_folha)
        try:
            turmas = parsear_turmas(tabelas_folha)
        except Exception as _:
            logger.exception(f" -     Não foi possível parsear turmas de {codigo} - {materia[1]}")
            return

        if not turmas:
            logger.warning(f" -      Disciplina {codigo} não possui turmas válidas cadastradas no Jupiter. Ignorando...")
            return

        logger.debug(f" -      Obtendo informações de {codigo} - {materia[1]}")
        try:
            response2 = await session.get('https://uspdigital.usp.br/jupiterweb/obterDisciplina?print=true&sgldis=' + codigo, timeout=args.timeout, ssl=False)
            assert response2.status == 200
            response2_text = await response2.text()
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f" -      Erro ao obter informações de {codigo}: {e}. Tentando novamente...")
            try:
                response2 = await session.get('https://uspdigital.usp.br/jupiterweb/obterDisciplina?print=true&sgldis=' + codigo, timeout=args.timeout * 2, ssl=False)
                assert response2.status == 200
                response2_text = await response2.text()
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                logger.error(f" -      Erro ao obter informações de {codigo} após nova tentativa: {e}")
                return
        except Exception as _:
            logger.exception(f" -      Não foi possível obter informações de {codigo}")
            return

        soup = BeautifulSoup(response2_text, "html.parser")
        tabelas_folha = soup.find_all(eh_tabela_folha)
        try:
            materia_info = parsear_info_materia(tabelas_folha)
        except Exception as _:
            logger.exception(f" -     Não foi possível parsear informações de {codigo} - {materia[1]}")
            return

        if not materia_info:
            logger.warning(f" -      Disciplina {codigo} não possui informações cadastradas no Jupiter. Ignorando...")
            return

        materia_info['turmas'] = turmas

        logger.debug(f" -      Salvando {codigo}")

        materia_json = json.dumps(materia_info)

        with open(os.path.join(args.diretorio_destino, f"{codigo}.json"), "w") as arq:
            arq.write(materia_json)
        if not args.nogzip:
            with gzip.open(os.path.join(args.diretorio_destino, f"{codigo}.json.gz"), "wb") as arq:
                arq.write(materia_json.encode('utf-8'))

        return materia_info

def parsear_info_materia(tabelas_folha):
    info = {}

    re_nome = re.compile(r"Disciplina:\s+.{7}\s+-.+")
    re_creditos = re.compile(r"Créditos\s+Aula")

    for folha in tabelas_folha:
        trs = folha.find_all("tr")
        if folha.find(text=re_nome):
            strings = list(folha.stripped_strings)
            info['unidade'] = strings[0]
            info['departamento'] = strings[1]
            info['campus'] = campus_por_unidade.get(int(codigos_unidades[info['unidade']]), "Outro")
            search = re.search(r"Disciplina:\s+([A-Z0-9\s]{7})\s-\s(.+)", strings[2])
            assert search is not None, f"{strings[2]} não é um nome de disciplina válido ({folha})"
            info['codigo'] = search.group(1).strip()
            info['nome'] = search.group(2)
        elif ''.join(trs[0].stripped_strings) == "Objetivos":
            info['objetivos'] = ''.join(trs[1].stripped_strings)
        elif ''.join(trs[0].stripped_strings) == "Programa Resumido":
            info['programa_resumido'] = ''.join(trs[1].stripped_strings)
        elif folha.find(text=re_creditos):
            info.update(parsear_creditos(folha))
    return info

def parsear_turmas(tabelas_folha):
    turmas = []
    info = horario = vagas = None
    for folha in tabelas_folha:
        if folha.find_all(text=re.compile(r"Código\s+da\s+Turma", flags=re.UNICODE)):
            if info is not None:
                if not horario:
                    logger.warn(f" -      Turma {info.get('codigo', 'desconhecido')} não possui horário cadastrado")
                elif not vagas:
                    logger.warn(f" -      Turma {info.get('codigo', 'desconhecido')} não possui vagas cadastradas")
                else:
                    info['horario'] = horario
                    info['vagas'] = vagas
                    turmas.append(info)
            info = parsear_info_turma(folha)
        elif folha.find_all(text="Horário"):
            horario = parsear_horario(folha)
        elif folha.find_all(text=re.compile(r"Atividades\s+Didáticas", flags=re.UNICODE)):
            continue
        elif folha.find_all(text="Vagas"):
            vagas = parsear_vagas(folha)

    if info is not None:
        if not horario:
            logger.warn(f" -      Turma {info.get('codigo', 'desconhecido')} não possui horário cadastrado")
        elif not vagas:
            logger.warn(f" -      Turma {info.get('codigo', 'desconhecido')} não possui vagas cadastradas")
        else:
            info['horario'] = horario
            info['vagas'] = vagas
            turmas.append(info)
    else:
            logger.warn(f"Treta no {info}")

    return turmas

def parsear_creditos(tabela):
    creditos = {'creditos_aula': 0, 'creditos_trabalho': 0}
    for tr in tabela.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if not tds:
            continue
        if re.search(r"Créditos\s+Aula:", tds[0], flags=re.U):
            creditos['creditos_aula'] = int(tds[1]) if tds[1].isdigit() else 0
        elif re.search(r"Créditos\s+Trabalho:", tds[0], flags=re.U):
            creditos['creditos_trabalho'] = int(tds[1]) if tds[1].isdigit() else 0
    return creditos

def parsear_info_turma(tabela):
    info = {}
    for tr in tabela.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if re.search(r"Código\s+da\s+Turma\s+Teórica", tds[0], flags=re.U):
            info['codigo_teorica'] = tds[1]
        elif re.search(r"Código\s+da\s+Turma", tds[0], flags=re.U):
            info['codigo'] = re.match(r"^(\w+)", tds[1], flags=re.U).group(1)
        elif re.search(r"Início", tds[0], flags=re.U):
            try:
                info['inicio'] = date_parser.parse(tds[1], dayfirst=True).strftime("%d/%m/%Y")
            except (date_parser.ParserError, IndexError):
                pass  # Handle cases where date parsing fails
        elif re.search(r"Fim", tds[0], flags=re.U):
            try:
                info['fim'] = date_parser.parse(tds[1], dayfirst=True).strftime("%d/%m/%Y")
            except (date_parser.ParserError, IndexError):
                pass
        elif re.search(r"Tipo\s+da\s+Turma", tds[0], flags=re.U):
            info['tipo'] = tds[1]
        elif re.search(r"Observações", tds[0], flags=re.U):
            info['observacoes'] = tds[1]
    return info

def parsear_vagas(tabela):
    vagas = {}
    accum = None
    for tr in tabela.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]

        if len(tds) == 5 and not tds[0]:
            continue
        elif len(tds) == 5 and tds[0]:
            if accum is not None:
                vagas[tipo] = accum
            tipo = tds[0]
            accum = {'vagas': int(tds[1]) if tds[1].isdigit() else 0, 'inscritos': int(tds[2]) if tds[2].isdigit() else 0,
                     'pendentes': int(tds[3]) if tds[3].isdigit() else 0, 'matriculados': int(tds[4]) if tds[4].isdigit() else 0,
                     'grupos': {}}
        elif len(tds) == 6:
            grupo = tds[1]
            detalhamento = {'vagas': int(tds[2]) if tds[2].isdigit() else 0, 'inscritos': int(tds[3]) if tds[3].isdigit() else 0,
                            'pendentes': int(tds[4]) if tds[4].isdigit() else 0, 'matriculados': int(tds[5]) if tds[5].isdigit() else 0}
            if accum is not None:  # Check if accum has been initialized
                accum['grupos'][grupo] = detalhamento
    if accum is not None:
        vagas[tipo] = accum
    return vagas

def parsear_horario(tabela):
    horario = []
    accum = None
    for tr in tabela.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if tds[0] == "Horário":
            continue

        if tds[0]:
            if accum is not None:
                horario.append(accum)
            accum = (tds[0], tds[1], tds[2], [tds[3]])

        elif not tds[0] and not tds[1]:
            if tds[2] > accum[2]:
                accum = (accum[0], accum[1], tds[2], accum[3] + [tds[3]])
            else:  # Add professor to the current entry
                accum = (accum[0], accum[1], accum[2], accum[3] + [tds[3]])

        elif not tds[0] and tds[1]:
            if accum is not None:
                horario.append(accum)
            accum = (accum[0], tds[1], tds[2], [tds[3]])

    if accum is not None:
        horario.append(accum)

    return [dict(zip(('dia', 'inicio', 'fim', 'professores'), entry)) for entry in horario]

def extrai_materia(x):
    search = re.search(r"sgldis=([A-Z0-9\s]{7})", x.get('href'))
    return (search.group(1).strip(), x.string) if search else None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Crawler MatrUSP")
    parser.add_argument('diretorio_destino', help="diretório que irá conter os arquivos resultantes")
    parser.add_argument('-v', '--verbosidade', action='count', default=0)
    parser.add_argument('-u', '--unidades', help="iterar apenas estes códigos de unidade", nargs='+')
    parser.add_argument('-s', '--simultaneidade', help="número de pedidos HTTP simultâneos", type=int, default=100)
    parser.add_argument('-t', '--timeout', help="tempo máximo (segundos) do pedido HTTP", type=int, default=120)
    parser.add_argument('-o', '--out', help="arquivo de saída do banco de dados completo", type=str, default="db.json")
    parser.add_argument('--nogzip', help="não compactar os arquivos de saída", action='store_true')
    args = parser.parse_args()

    if not os.path.isdir(args.diretorio_destino):
        parser.print_help()
        exit(1)

    logger = logging.getLogger('log')
    logger.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(60 - 10 * (args.verbosidade or 4)) # Changed for clarity and conciseness
    ch.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(ch)

    fh = logging.FileHandler(time.strftime('%Y-%m-%d_%H-%M-%S_' + os.path.basename(__file__) + '.log'))
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter('[%(asctime)s] %(module)s %(levelname)s: %(message)s'))
    logger.addHandler(fh)

    async def run():
        global semaforo, args
        semaforo = asyncio.Semaphore(args.simultaneidade)
        await main()

    try:
        asyncio.run(run())
    except Exception as _:
        logger.exception("Uncaught exception during asyncio.run")
