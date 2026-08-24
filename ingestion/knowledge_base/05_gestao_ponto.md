# Funcionalidade: Gestão de Ponto

## O que faz

Substitui o ponto em papel, planilha ou relógio de ponto físico por um
app de celular com registro de geolocalização, atendendo às exigências
da Portaria 671/2021 do Ministério do Trabalho (ponto por dispositivo
móvel).

## Como funciona

- Funcionário bate o ponto pelo app, que registra horário e
  localização.
- Sistema calcula automaticamente horas trabalhadas, horas extras,
  banco de horas e faltas.
- Inconsistências (esquecimento de bater ponto, jornada fora do
  padrão) geram alerta pro gestor antes do fechamento da folha, não
  depois.
- Gestor aprova ou ajusta batidas direto pelo painel, com histórico de
  alterações registrado (auditoria).

## Banco de horas

Cálculo automático de banco de horas conforme acordo individual ou
convenção coletiva cadastrada, com alerta quando o saldo se aproxima do
limite legal de compensação.

## Geolocalização

O registro de localização serve como comprovação em caso de
fiscalização, mas não bloqueia o funcionário de bater ponto fora do
raio esperado — apenas sinaliza a divergência pro gestor revisar.

## Integração

O módulo de ponto alimenta diretamente o cálculo da folha — não existe
etapa de exportar/importar planilha entre os dois módulos, ao contrário
de configurações comuns onde ponto e folha são sistemas separados.
