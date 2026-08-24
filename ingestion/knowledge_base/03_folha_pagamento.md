# Funcionalidade: Folha de Pagamento

## O que faz

Calcula a folha de pagamento mensal automaticamente, incluindo:

- Salário base, horas extras e faltas
- Descontos obrigatórios: INSS, IRRF, FGTS
- Adicionais: noturno, insalubridade, periculosidade
- 13º salário e férias (cálculo proporcional automático)
- Rescisão (cálculo de verbas rescisórias completo)

## Como funciona

O cálculo puxa automaticamente as horas trabalhadas do módulo de ponto
(sem precisar exportar/importar planilha entre sistemas) e aplica as
regras da CLT e da convenção coletiva cadastrada pra cada
funcionário. O RH revisa um resumo antes de fechar a folha, com
qualquer inconsistência (falta não justificada, horas extras acima do
limite legal) sinalizada antes do fechamento, não depois.

## Holerite digital

Cada funcionário recebe o holerite direto no app, com histórico de
todos os meses anteriores disponível. Reduz drasticamente perguntas de
RH sobre "cadê meu holerite".

## Pagamento

Integração direta com os principais bancos pra geração do arquivo de
pagamento (CNAB) ou pagamento via PIX em lote, direto da plataforma —
sem precisar exportar planilha pro internet banking.

## Limitação atual

A Helssing calcula a folha com base nas regras da CLT padrão e
convenções coletivas cadastradas manualmente pelo cliente — não
monitora automaticamente mudanças de convenção coletiva em tempo real
(o RH precisa atualizar quando a convenção da categoria mudar).
