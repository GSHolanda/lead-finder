# Lead Finder — empresas sem site próprio em Brasília/DF e entorno

Ferramenta local e gratuita para encontrar estabelecimentos de Brasília/DF e
entorno que **não têm site próprio** e oferecer a eles **desenvolvimento de
sites**, com verificação cuidadosa da existência de site, pesquisa baseada em
informações públicas, análise local com **Ollama** e **revisão humana antes de
qualquer contato** (a ferramenta nunca envia mensagens).

- Só Python 3.10+ (biblioteca padrão, sem dependências externas).
- Dados ficam em um arquivo SQLite local (`data/leadfinder.db`).
- Interface web local em `http://127.0.0.1:8765`.

## Fluxo

```
Descoberta ─► Verificação de site ─► Pesquisa sem site ─► Qualificação ─► Mensagem ─► Revisão humana ─► envio manual
                     ▲                                                                       │
                     └──────────── Piloto: revisão de amostra e medição de erros ◄───────────┘
```

### 1. Descoberta (fontes gratuitas)

| Fonte | O que traz | Observações |
|---|---|---|
| **OpenStreetMap** (Overpass + Nominatim) | nome, segmento, endereço, telefone, links de site/redes | DF inteiro, Região Administrativa (busca por raio) ou município do entorno (GO). Dados © colaboradores do OSM (ODbL). |
| **Dados abertos do CNPJ** (Receita Federal) | nome fantasia, CNAE, endereço, telefone, e-mail | Só estabelecimentos ativos e com nome fantasia. Telefone/e-mail podem ser do contador — a interface avisa. |
| **CSV** montado por você | qualquer lista (nome, telefone, site, instagram, bairro…) | colunas reconhecidas: `nome`, `telefone`, `whatsapp`, `email`, `site`, `instagram`, `facebook`, `endereco`, `numero`, `bairro`, `cidade`, `uf`, `cep`, `cnpj`, `segmento`. |
| **Cadastro manual** | pela interface | |

Candidatos cujo campo de site está vazio ou só tem redes sociais, agregadores,
delivery ou WhatsApp recebem **prioridade alta**. Nenhum candidato é descartado
nem considerado "sem site" antes da verificação.

Leads de fontes diferentes são mesclados por CNPJ, por telefone (com um termo
distintivo do nome em comum) ou por nome + bairro.

### 2. Verificação de site próprio

Cada passo vira um registro com **data, fonte, consulta/URL e resultado**:

1. **Links dos registros** das fontes. Domínios próprios são acessados.
   Encurtadores são resolvidos.
2. **Agregadores de links** da própria empresa (Linktree etc.). São lidos quando
   o robots.txt permite, e os links de saída entram na verificação.
3. **Domínio do e-mail comercial**, se não for Gmail/Hotmail etc.
4. **Busca web** por *nome + cidade*, *nome + bairro* e *telefone*, que ajuda a
   distinguir homônimos.
5. **Domínios prováveis** (`nome.com.br`, `nome.com`). Opcional, e só aceitos
   com evidência de conteúdo.

**Associação de um site à empresa.** Nome parecido **nunca** basta. É preciso:
- **evidência forte:** telefone, CNPJ ou e-mail do lead na página; ou
- **evidência média + nome:** CEP ou endereço mais o nome; ou
- **link vindo da própria fonte/empresa**, sem conteúdo contraditório.

Páginas que listam muitas empresas (guias/diretórios) são descartadas.

**Classificações:**

| # | Classificação | Quando |
|---|---|---|
| 1 | Site próprio encontrado | site associado com evidência ou pela fonte |
| 2 | Somente redes sociais ou plataformas | busca suficiente sem site próprio; há Instagram/WhatsApp/iFood… associados |
| 3 | Site próprio não localizado após verificação | busca suficiente, nada associado |
| 4 | Verificação inconclusiva | busca falhou ou não está configurada, ou há candidato que não pôde ser confirmado nem descartado |
| 5 | Possível site indisponível | link de site da fonte fora do ar, estacionado ou em construção |

Regras de prudência:
- Falha de consulta **não** significa ausência de site: o resultado fica como inconclusivo.
- Domínio fora do ar **não** significa ausência de site: o resultado fica como possível site indisponível.
- Sem provedor de busca configurado, nada passa de "inconclusiva". Use os links de busca manual e registre a correção.
- A ferramenta respeita robots.txt e não contorna bloqueios (401/403/429, captchas).
- Não exige login: Instagram e Facebook não são lidos automaticamente. Registramos só o link. Para usar o conteúdo, cole o texto público como material.

**Correções humanas.** Cada lead pode ser corrigido manualmente, informando a
origem da correção (pesquisa manual, confirmação da empresa ou piloto). A
correção prevalece e **nunca é sobrescrita** por verificações automáticas. Se
a verificação automática discordar, a divergência fica registrada no
histórico. Todo o histórico é preservado.

### 3. Pesquisa sem site

Fatos coletados apenas de informações públicas, **cada um com sua fonte**:
- campos das fontes (horário, cardápio/culinária, delivery, especialidades);
- presença em redes e plataformas;
- páginas de agregadores;
- materiais colados por você.

O Ollama extrai serviços, produtos, região atendida, formas de contato e
horário. Cada item precisa citar um **trecho literal** do texto de origem; itens
cujo trecho não existe são descartados. Quando há pouca presença digital, o
resumo diz isso, sem completar lacunas. Você pode remover uma informação, e ela
não volta em novas análises.

### 4. Qualificação (0–100, com composição visível)

| Componente | Peso |
|---|---|
| Site próprio não localizado × confiança da verificação | 35 |
| Aderência ao serviço (segmentos-alvo) | 20 |
| Contato comercial (WhatsApp/celular > fixo > e-mail) | 20 |
| Benefício plausível e específico de um site (com evidência) | 15 |
| Qualidade das evidências | 10 |

As possíveis aplicações de um site (catálogo/cardápio, apresentação de
serviços, orçamento, portfólio, localização, botão de WhatsApp, agendamento) só
contam quando há evidência da própria empresa. As sugestões típicas do segmento
aparecem separadas.

O sistema não presume faturamento, orçamento, interesse ou perda de clientes.

**Listas:**
- Empresas com site encontrado vão para **"Com site — lista separada"** e saem da campanha.
- Redes e franquias (tag `brand` no OSM) ficam fora até você revisar.

### 5. Abordagem

A mensagem é gerada pelo Ollama, ou por um modelo de texto padrão quando o
Ollama não está disponível ou o texto dele viola as regras. Ela é sempre
editável e versionada.

**Regras que bloqueiam a aprovação:**
- afirmar ausência de site não confirmada pela própria empresa (a mensagem pergunta *"Vocês já têm um site próprio?"*);
- criticar a presença digital;
- prometer vendas, clientes ou posição no Google;
- dizer que já criou site, projeto, análise ou proposta;
- presumir faturamento, orçamento ou necessidade;
- deixar campos `[entre colchetes]`.

A aprovação só é possível para empresas sem site próprio localizado. Depois de
aprovada, a interface oferece copiar, abrir no WhatsApp ou abrir no e-mail. O
**envio é sempre manual**.

### 6. Validação do piloto

1. Na aba **Piloto**, sorteie uma amostra aleatória (padrão: 30) de leads classificados automaticamente como "site próprio não localizado" (opcionalmente incluindo "somente redes/plataformas").
2. Para cada item, pesquise você mesmo (Google, Bing, Maps). Se encontrar o site, registre a URL e **como encontrou**. A revisão vira correção humana no lead.
3. O relatório mostra:
   - a **taxa de sites não encontrados**, com intervalo de confiança de 95% (Wilson);
   - recortes por classificação, confiança e segmento;
   - um **diagnóstico de cada erro**: o site nunca apareceu na busca, apareceu e não foi confirmado, estava bloqueado, estava na bio do Instagram, no Google Maps, em domínio parecido…;
   - **recomendações** de melhoria.
4. **Decisão:** só aumente o volume quando o limite superior do erro estiver abaixo do limite configurado (padrão 10%) e houver revisões suficientes (padrão 20).
5. Depois de ajustar a verificação, use **"Reverificar esses casos"** para medir quantos erros passam a ser detectados.

## Instalação

```bash
git clone https://github.com/GSHolanda/lead-finder.git
```

```bash
cd lead-finder
```

```bash
python -m leadfinder serve
```

Abra `http://127.0.0.1:8765`. Em **Configurações**, preencha seu nome (ele
assina as mensagens) e o provedor de busca.

### Ollama (análise local)

Instale em <https://ollama.com> e baixe um modelo com bom português, por exemplo:

```bash
ollama pull qwen2.5:7b
```

O modelo pode ser trocado em Configurações (a lista mostra os modelos instalados).

### Busca web gratuita (recomendado: SearXNG local)

Sem um provedor de busca, a verificação nunca conclui "sem site". Para rodar o
SearXNG na sua máquina:

```bash
docker run -d --name searxng -p 8888:8080 -v "$PWD/searxng:/etc/searxng" searxng/searxng
```

Em `searxng/settings.yml`, habilite o formato JSON:

```yaml
search:
  formats:
    - html
    - json
```

Reinicie o contêiner e escolha `searxng` em Configurações (endereço
`http://localhost:8888`).

Alternativa: **Brave Search API**, que tem plano gratuito com limite mensal
(confira as condições atuais). A chave fica só no seu banco local, ou na
variável `LEADFINDER_BRAVE_KEY`.

## Linha de comando

```bash
python -m leadfinder segmentos
```

```bash
python -m leadfinder descobrir --segmento beleza --ra "Taguatinga" --raio 3000
```

```bash
python -m leadfinder descobrir --segmento automotivo --municipio "Valparaíso de Goiás"
```

```bash
python -m leadfinder importar-csv minha_lista.csv --cidade Brasília --uf DF
```

```bash
python -m leadfinder importar-cnpj Estabelecimentos0.csv --uf DF --segmento beleza --segmento saude
```

```bash
python -m leadfinder processar --status pendente --limit 50
```

```bash
python -m leadfinder corrigir-site 42 site_proprio --url https://exemplo.com.br --fonte pesquisa_manual
```

```bash
python -m leadfinder listar --view campanha
```

```bash
python -m leadfinder piloto-amostra --tamanho 30
```

```bash
python -m leadfinder piloto-relatorio 1
```

```bash
python -m leadfinder exportar --view campanha --saida campanha.csv
```

Para o entorno pelos dados do CNPJ, use a tabela de municípios da Receita:

```bash
python -m leadfinder importar-cnpj Estabelecimentos0.csv --uf GO --municipios-arquivo Municipios.csv --cidade "Valparaiso de Goias"
```

## Testes

```bash
python -m pytest
```

Os testes não acessam a internet: rede, busca e Ollama são simulados. O cliente
HTTP é testado contra um servidor local.

## Uso responsável

- Use as instâncias públicas do Overpass e do Nominatim com moderação (uma região/segmento por vez). O intervalo entre acessos ao mesmo site é configurável.
- Os dados tratados são de empresas, publicados por elas ou em bases públicas. Use-os só para a abordagem comercial (B2B) e respeite pedidos para não ser contatado (marque "Sem interesse" no funil).
- Nenhum contato é feito automaticamente.

## Limitações conhecidas

- A cobertura do OSM no DF é incompleta. Combine com CSV e com os dados do CNPJ.
- Telefones publicados só em imagens não são lidos; o site pode ficar como "inconclusiva".
- Instagram, Facebook e Google Maps não são consultados automaticamente. O piloto mede quanto isso afeta o resultado.
- Os prefixos CNAE por segmento são uma referência. Revise-os para o seu nicho em `leadfinder/discovery/segments.py`.
