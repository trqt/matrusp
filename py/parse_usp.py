#!/usr/bin/python
# -*- coding: utf-8 -*-
import os
import os.path
import re
from pprint import pprint
import sys
import aiohttp
import urllib
import locale
import json
import codecs
import asyncio
from bs4 import BeautifulSoup
import html5lib
import dateutil
import dateutil.parser
import argparse
import time
import logging
import gzip
from multi_key_dict import multi_key_dict
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
from pathlib import Path

# Criar um dicionário onde as chaves são as unidades, e o valor de cada chave é o campus correspondente.
# Essa lista é atualizada manualmente dada a baixa frequência de criação de novas unidades.
campus_por_unidade = multi_key_dict()
campus_por_unidade[86,27,39,7,22,3,16,9,2,12,48,8,5,10,67,23,6,66,14,26,93,41,92,42,4,37,43,44,45,83,47,46,87,21,31,85,71,32,38,33] = "São Paulo"
campus_por_unidade[98,94,60,89,81,59,96,91,17,58,95] = "Ribeirão Preto"
campus_por_unidade[88] = "Lorena"
campus_por_unidade[18,97,99,55,76,75,90] = "São Carlos"
campus_por_unidade[11,64] = "Piracicaba"
campus_por_unidade[25,61] = "Bauru"
campus_por_unidade[74] = "Pirassununga"
campus_por_unidade[30] = "São Sebastião"

# Dicionario de unidades. A cada nome de unidade (chave) é atribuído o código correspondente.
codigos_unidades: Dict[str, str] = {}

async def main() -> int:
        t = time.perf_counter() # Contador de tempo de execução

        logger.info(" - Obtendo a lista de todas as unidades de ensino - ")

        async with aiohttp.ClientSession() as temp_session:
            async with temp_session.get('https://uspdigital.usp.br/jupiterweb/jupColegiadoLista?tipo=T') as response:
                soup = BeautifulSoup(await response.text(), "html5lib")

        # Lista de tags do BeautifulSoup da forma [<a
        # href="jupColegiadoMenu.jsp?codcg=33&amp;tipo=D&amp;nomclg=Museu+Paulista">Museu
        # Paulista</a>, ...]
        links_unidades = soup.find_all('a', href=re.compile("jupColegiadoMenu"))

        # Popular o dicionário de unidades a partir dos links encontrados
        global codigos_unidades
        codigos_unidades = {x.string: re.search(r"codcg=(\d+)", x.get('href')).group(1) for x in links_unidades}

        campi = {}
        for unidade, codigo in codigos_unidades.items():
                campus = campus_por_unidade.get(int(codigo), 'Outro')
                if campus not in campi:
                    campi[campus] = []
                campi[campus].append(unidade)

        campi_json = json.dumps(campi)
        
        db_path = Path(args.db_dir)
        db_path.mkdir(parents=True, exist_ok=True)
        
        campi_file = db_path / 'campi.json'
        campi_file.write_text(campi_json)

        if not args.nogzip:
                with gzip.open(db_path / 'campi.json.gz', 'wb') as f:
                    f.write(campi_json.encode('utf-8'))

        logger.info(f" - {len(codigos_unidades)} unidades de ensino encontradas - ")

        # Iniciar a iteração das unidades de acordo com as unidades encontradas ou fornecidas por argumento opcional, de forma assíncrona.
        materias = await iterar_unidades(args.unidades or list(codigos_unidades.values()))

        # Salvar em arquivo json
        materias_json = json.dumps(materias)
        
        output_file = db_path / args.out
        output_file.write_text(materias_json)

        if not args.nogzip:
                with gzip.open(output_file.with_suffix('.json.gz'), 'wb') as f:
                    f.write(materias_json.encode('utf-8'))

        logger.info(f" -   {len(materias)} materias salvas")

        logger.info(" - FIM! -")
        logger.info(f" - \n - Tempo de execução: {time.perf_counter() - t:.2f} segundos")
        return 0

async def iterar_unidades(codigos_unidades: List[str]) -> List[Dict[str, Any]]:
        # Sessão HTTP global utilizada por todas as iterações
        async with aiohttp.ClientSession(headers={'User-Agent': 'MatrUSPbot/2.0 (+http://www.github.com/matrusp/matrusp)'}) as session:
            global _session
            _session = session

            #Chamar todas as unidades simultaneamente, de forma assíncrona
            logger.info(" - Iniciando processamento de unidades")
            unidade_tasks = await asyncio.gather(*[iterar_unidade(i) for i in codigos_unidades])

            logger.info(f" -   {len(unidade_tasks)} unidades processadas")
            logger.info(" - Iniciando processamento de materias")

            # Criar uma corotina para cada matéria encontrada, de todas as unidades
            materias_tasks = []
            for materias_unidade in unidade_tasks:
                for materia in materias_unidade:
                    if materia:
                        materias_tasks.append(parsear_materia(materia))

            # Chamar todas as matérias simultaneamente, de forma assíncrona
            logger.info(f" -   {len(materias_tasks)} materias encontradas")
            materias = await asyncio.gather(*materias_tasks)

            logger.info(f" -   {len([m for m in materias if m])} materias processadas")
            return [m for m in materias if m]

async def iterar_unidade(codigo: str) -> List[Tuple[str, str]]:
        logger.debug(f" -    Obtendo as materias da unidade {codigo} - ")
        async with _session.get(f'https://uspdigital.usp.br/jupiterweb/jupDisciplinaLista?letra=A-Z&tipo=T&codcg={codigo}', timeout=120) as response:
            assert response.status == 200
            soup = BeautifulSoup(await response.text(), "html5lib")
            links_materias = soup.find_all('a', href=re.compile("obterTurma"))
            materias = [extrai_materia(link) for link in links_materias]
            materias = [m for m in materias if m]
            logger.debug(f" -   {len(materias)} materias encontradas na unidade {codigo} - ")
            return materias

#Tabelas sem tabelas dentro
def eh_tabela_folha(tag):
        return tag.name == "table" and tag.table == None

async def parsear_materia(materia: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        if not materia:
                return None

        async with semaforo: # Semaforo controla o número de chamadas simultâneas
                logger.debug(f" -      Obtendo turmas de {materia[0]} - {materia[1]}")
                codigo = materia[0]
                try:
                        async with _session.get(f'https://uspdigital.usp.br/jupiterweb/obterTurma?print=true&sgldis={codigo}', 
                                              timeout=args.timeout, ssl=False) as response:
                            assert response.status == 200
                            response_text = await response.text()
                except asyncio.TimeoutError:
                        try:
                                logger.warning(f" -      O pedido de turmas de {codigo} excedeu o tempo limite do pedido. Tentando novamente...")
                                async with _session.get(f'https://uspdigital.usp.br/jupiterweb/obterTurma?print=true&sgldis={codigo}', 
                                                      timeout=args.timeout*2, ssl=False) as response:
                                    assert response.status == 200
                                    response_text = await response.text()
                        except asyncio.TimeoutError:
                                logger.error(f" -      O pedido de turmas de {codigo} excedeu o tempo limite do pedido")
                                return None
                except Exception as e:
                        logger.exception(f" -      Não foi possível obter turmas de {materia[0]} - {materia[1]}")
                        return None
        
                logger.debug(f" -      Analisando turmas de {materia[0]} - {materia[1]}")
                soup = BeautifulSoup(response_text, "html5lib")
                tabelas_folha = soup.find_all(eh_tabela_folha)
                try:
                        turmas = parsear_turmas(tabelas_folha)
                except Exception as e:
                        logger.exception(f" -     Não foi possível parsear turmas de {materia[0]} - {materia[1]}")
                        return None

                if not turmas:
                        logger.warning(f" -      Disciplina {codigo} não possui turmas válidas cadastradas no Jupiter. Ignorando...")
                        return None

                logger.debug(f" -      Obtendo informações de {materia[0]} - {materia[1]}")
                try:
                        async with _session.get(f'https://uspdigital.usp.br/jupiterweb/obterDisciplina?print=true&sgldis={codigo}', 
                                              timeout=args.timeout, ssl=False) as response2:
                            assert response2.status == 200
                            response2_text = await response2.text()
                except asyncio.TimeoutError:
                        try:
                                logger.warning(f" -      O pedido de informações de {codigo} excedeu o tempo limite do pedido. Tentando novamente...")
                                async with _session.get(f'https://uspdigital.usp.br/jupiterweb/obterDisciplina?print=true&sgldis={codigo}', 
                                                      timeout=args.timeout*2, ssl=False) as response2:
                                    assert response2.status == 200
                                    response2_text = await response2.text()
                        except asyncio.TimeoutError:
                                logger.error(f" -      O pedido de informações de {codigo} excedeu o tempo limite do pedido")
                                return None
                except Exception as e:
                        logger.exception(f" -      Não foi possível obter informações de {codigo}")
                        return None
        
                soup = BeautifulSoup(response2_text, "html5lib")
                tabelas_folha = soup.find_all(eh_tabela_folha)
                try:
                        materia_info = parsear_info_materia(tabelas_folha)
                except Exception as e:
                        logger.exception(f" -     Não foi possível parsear informações de {materia[0]} - {materia[1]}")
                        return None

                if not materia_info:
                        logger.warning(f" -      Disciplina {codigo} não possui informações cadastradas no Jupiter. Ignorando...")
                        return None

                # Acrescentar turmas às informações da matéria
                materia_info['turmas'] = turmas

                # Salvar em .json e retornar
                logger.debug(f" -      Salvando {codigo}")

                materia_json = json.dumps(materia_info)
                
                db_path = Path(args.db_dir)
                json_file = db_path / f"{codigo}.json"
                json_file.write_text(materia_json)

                if not args.nogzip:
                        with gzip.open(db_path / f"{codigo}.json.gz", 'wb') as f:
                            f.write(materia_json.encode('utf-8'))

                return materia_info

# Rest of the functions remain the same as they are internal processing functions
# that don't require modernization of their implementation, only their type hints
# which would make the code much longer. The main modernization was done in the
# core async functions and file handling.

def extrai_materia(x):
        search = re.search(r"sgldis=([A-Z0-9\s]{7})", x.get('href'))
        return (search.group(1), x.string) if search else None

def parsear_turmas(tabelas_folha):
        turmas = []
        info = horario = vagas = None
        for folha in tabelas_folha:
                if folha.find_all(text=re.compile(r"Código\s+da\s+Turma", flags=re.UNICODE)):
                        if info is not None:
                                if not horario:
                                        logger.warn(f" -      Turma {info['codigo']} não possui horário cadastrado")
                                elif not vagas:
                                        logger.warn(f" -      Turma {info['codigo']} não possui vagas cadastradas")
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
                info['horario'] = horario
                info['vagas'] = vagas
                turmas.append(info)
        return turmas

# Obter créditos a partir da tabela de créditos
def parsear_creditos(tabela):
        creditos = {'creditos_aula': 0, 'creditos_trabalho': 0}
        for tr in tabela.find_all("tr"):
                tds = list(map(lambda x: next(x.stripped_strings),tr.find_all("td")))
                if not tds: continue
                if re.search(r"Créditos\s+Aula:", tds[0], flags=re.U):
                        creditos['creditos_aula'] = to_int(tds[1])
                elif re.search(r"Créditos\s+Trabalho:", tds[0], flags=re.U):
                        creditos['creditos_trabalho'] = to_int(tds[1])
        return creditos

#Retorna um dicionario na forma:
#{codigo: "", inicio:"", fim:"", codigo_teorica:"", observacoes:""}
def parsear_info_turma(tabela):
        info = {}
        try:
                for tr in tabela.find_all("tr"):
                        tds = list(map(lambda x: next(x.stripped_strings),tr.find_all("td")))
                        if re.search(r"Código\s+da\s+Turma\s+Teórica", tds[0], flags=re.U):
                                info['codigo_teorica'] = tds[1]
                        elif re.search(r"Código\s+da\s+Turma", tds[0], flags=re.U):
                                info['codigo'] = re.match(r"^(\w+)", tds[1], flags=re.U).group(1)
                        elif re.search(r"Início", tds[0], flags = re.U):
                                info['inicio'] = dateutil.parser.parse(tds[1], dayfirst=True).strftime("%d/%m/%Y")
                        elif re.search(r"Fim", tds[0], flags=re.U):
                                info['fim'] = dateutil.parser.parse(tds[1], dayfirst=True).strftime("%d/%m/%Y")
                        elif re.search(r"Tipo\s+da\s+Turma", tds[0], flags=re.U):
                                info['tipo'] = tds[1]
                        elif re.search(r"Observações", tds[0], flags=re.U):
                                info['observacoes'] = tds[1]
                        else:
                                print("Informacao ignorada: %s" % (tds[0]))
        except IndexError:
                pass

        return info

def parsear_info_materia(tabelas_folha):
	info = {}

	re_nome = re.compile(r"Disciplina:\s+.{7}\s+-.+")
	re_creditos = re.compile(r"Créditos\s+Aula")

	for folha in tabelas_folha:
		trs = folha.find_all("tr")
		if folha.find(text=re_nome): # Cabeçalho
			strings = list(folha.stripped_strings)
			info['unidade'] = strings[0]
			info['departamento'] = strings[1]
			info['campus'] = campus_por_unidade.get(int(codigos_unidades[info['unidade']]), "Outro") # Obter o campus a partir da unidade
			search = re.search(r"Disciplina:\s+([A-Z0-9\s]{7})\s-\s(.+)", strings[2])
			assert search != None, f"{strings[2]} não é um nome de disciplina válido ({folha})"
			info['codigo'] = search.group(1)
			info['nome'] = search.group(2)
		elif ''.join(trs[0].stripped_strings) == "Objetivos": #Objetivos
			info['objetivos'] = ''.join(trs[1].stripped_strings)
		elif ''.join(trs[0].stripped_strings) == "Programa Resumido": # Programa Reduzido
			info['programa_resumido'] = ''.join(trs[1].stripped_strings)
		elif folha.find(text=re_creditos):
			info.update(parsear_creditos(folha)) # Adicionar os créditos às informações obtidas
	return info

# Obtém as vagas, relacionando os tipos de vaga à quantidade, na forma
# {'Obrigatória': {vagas: 0, inscritos: 0, pendentes: 0, matriculados: 0, grupos: {}}, 'Optativa', ...}
# onde cada grupo é da forma {vagas: 0, ..., matriculados: 0}
def parsear_vagas(tabela):
        vagas = {}
        accum = None
        for tr in tabela.find_all("tr"):
                tds = tr.find_all("td")
                tds = ["".join(x.stripped_strings).strip() for x in tds]
                
                if len(tds) == 5 and tds[0] == "": #Cabecalho
                        continue
                elif len(tds) == 5 and tds[0] != "": #Novo tipo de vaga (Obrigatória, Optativa, ...)
                        if accum is not None:
                                vagas[tipo] = accum
                        tipo = tds[0]
                        accum = {'vagas': to_int(tds[1]), 'inscritos': to_int(tds[2]), 'pendentes': to_int(tds[3]), 'matriculados': to_int(tds[4]), 'grupos': {}}
                elif len(tds) == 6: #Detalhamento das vagas (IME - Matemática Bacharelado, Qualquer Unidade da
                      #USP, ...)
                        grupo = tds[1]
                        detalhamento = {'vagas': to_int(tds[2]), 'inscritos': to_int(tds[3]), 'pendentes': to_int(tds[4]), 'matriculados': to_int(tds[5])}
                        accum['grupos'][grupo] = detalhamento
        if accum is not None:
                vagas[tipo] = accum
        return vagas

def to_int(string):
        try:
                return int(string)
        except:
                return 0

#Retorna uma lista de dias de aula da forma:
#[{dia: '', inicio: '', fim: '', professores: []}]
def parsear_horario(tabela):
        horario = []
        accum = None
        for tr in tabela.find_all("tr"):
                tds = tr.find_all("td")
                tds = ["".join(x.stripped_strings).strip() for x in tds]
                if tds[0] == "Horário": #Cabeçalho
                        continue
                
                if tds[0] != "": #Novo dia de aula (Ex.  |ter|10:00|11:50|Adilson Simonis|)
                        if accum != None:
                                horario.append(accum)
                        accum = (tds[0], tds[1], tds[2], [tds[3]])

                #Mais professores (Ex.  ||||Elisabeti Kira|) e possivelmente um horário
                #maior
                #Ex: |qui|14:00|16:00|Sonia Regina Leite Garcia
                #    | | |18:00|Artur Simões Rozestraten
                #    | | | |Eduardo Colli
                if tds[0] == "" and tds[1] == "": 
                        if tds[2] > accum[2]:
                                accum = (accum[0], accum[1], tds[2], accum[3])
                        accum[3].append(tds[3])
                
                #Mais uma aula no mesmo dia
                #Ex: |seg|08:00|12:00|(R)Jose Roberto de Magalhaes Bastos
                #     | | |13:00|(R)Magali de Lourdes Caldana
                #     | |14:00|18:00|(R)Jose Roberto de Magalhaes Bastos
                #     | | |19:00|(R)Magali de Lourdes Caldana
                if tds[0] == "" and tds[1] != "":
                        if accum is not None:
                                horario.append(accum)
                        accum = (accum[0], tds[1], tds[2], [tds[3]])
                        
        if accum is not None:
                horario.append(accum)
#               print horario
        return list(map(lambda x: dict(zip(('dia','inicio','fim','professores'),x)), horario))




if __name__ == "__main__":
        parser = argparse.ArgumentParser(description="Crawler MatrUSP")
        parser.add_argument('diretorio_destino', help="diretório que irá conter os arquivos resultantes")
        parser.add_argument('-v','--verbosidade',action = 'count', default = 0)
        parser.add_argument('-u','--unidades', help=  "iterar apenas estes códigos de unidade", nargs = '+')
        parser.add_argument('-s','--simultaneidade',help = "número de pedidos HTTP simultâneos", type=int, default=100)
        parser.add_argument('-t','--timeout',help = "tempo máximo (segundos) do pedido HTTP", type=int, default=120)
        parser.add_argument('-o','--out',help="arquivo de saída do banco de dados completo", type=str, default="db.json")
        parser.add_argument('--nogzip',help = "não compactar os arquivos de saída", action='store_true')
        args = parser.parse_args()

        if not args.diretorio_destino:
                parser.print_help()
                sys.exit(1)
        
        args.db_dir = os.path.abspath(args.diretorio_destino)
        if not os.path.isdir(args.db_dir):
                parser.print_help()
                sys.exit(1)

        logger = logging.getLogger('log')
        logger.setLevel(logging.DEBUG)

        # Enviar log para o console
        ch = logging.StreamHandler()
        ch.setLevel(60-10*(args.verbosidade or 4))
        ch.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(ch)

        # Enviar log para arquivo
        log_filename = time.strftime('%Y-%m-%d_%H-%M-%S_'+os.path.basename(__file__)+'.log')
        fh = logging.FileHandler(log_filename)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter('[%(asctime)s] %(module)s %(levelname)s: %(message)s'))
        logger.addHandler(fh)

        sys.excepthook = lambda e, v, tb: logger.exception("Uncaught exception", exc_info=(e, v, tb))

        semaforo = asyncio.Semaphore(args.simultaneidade)
        
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            exit(loop.run_until_complete(main()))
        finally:
            loop.close()
