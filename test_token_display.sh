#!/bin/bash

API_URL="https://web-production-126e2.up.railway.app/api/support"
API_KEY="dev-key-change-in-production"

echo "🧪 TESTE DE TOKENS - Validação do Fix"
echo "======================================"
echo ""

# Função para enviar ticket e mostrar resultado
send_ticket() {
    local num=$1
    local inquiry=$2
    
    echo "📝 Teste $num: $inquiry"
    echo "Enviando..."
    
    response=$(curl -s -X POST "$API_URL" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: $API_KEY" \
        -d "{\"inquiry\": \"$inquiry\", \"customer_email\": \"test@example.com\"}")
    
    ref_id=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('reference_id', 'N/A'))" 2>/dev/null)
    
    if [ "$ref_id" != "N/A" ]; then
        echo "✅ Ticket criado: $ref_id"
        echo "🔗 Ver em: https://web-production-126e2.up.railway.app/?ref=$ref_id"
    else
        echo "❌ Erro ao criar ticket"
    fi
    echo ""
    sleep 2
}

echo "🎯 TESTES PARA VALIDAÇÃO IMEDIATA (3)"
echo "--------------------------------------"
send_ticket 1 "Meu pedido #12345 ainda não chegou. Já faz 2 semanas!"
send_ticket 2 "Quero cancelar minha compra e solicitar reembolso"
send_ticket 3 "Não consigo acessar minha conta. Esqueci a senha"

echo ""
echo "⏳ Aguarde 30 segundos para os tickets processarem..."
sleep 30

echo ""
echo "📊 TESTES PARA DEMO (7)"
echo "----------------------"
send_ticket 4 "Produto chegou com defeito. Como faço para trocar?"
send_ticket 5 "Qual o prazo de entrega para o CEP 01310-100?"
send_ticket 6 "Meu notebook apresentou defeito após 3 meses de uso. A garantia cobre?"
send_ticket 7 "Recebi o produto errado. Pedi um celular e veio um tablet"
send_ticket 8 "Como faço para rastrear meu pedido?"
send_ticket 9 "Quero alterar o endereço de entrega do meu pedido"
send_ticket 10 "O produto está em promoção? Posso usar cupom de desconto?"

echo ""
echo "✅ TODOS OS TESTES ENVIADOS!"
echo ""
echo "🔍 Para verificar os tokens:"
echo "1. Acesse: https://web-production-126e2.up.railway.app"
echo "2. Clique em qualquer ticket"
echo "3. Vá na aba 'Observability'"
echo "4. Verifique se os tokens aparecem (não mais '--')"
echo ""
echo "📋 Lista de tickets criados acima ☝️"

# Made with Bob
