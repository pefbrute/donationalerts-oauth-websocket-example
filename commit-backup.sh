#!/bin/bash

# Загружаем переменные окружения из файла .env
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "Ошибка: файл .env не найден."
    exit 1
fi

# Функция для проверки успешного выполнения команды
check_command() {
    if [ $? -ne 0 ]; then
        echo "Ошибка: $1"
        exit 1
    fi
}

# Проверяем, установлен ли git
if ! command -v git &> /dev/null; then
    echo "Ошибка: git не установлен."
    exit 1
fi

# Проверяем, установлен ли jq
if ! command -v jq &> /dev/null; then
    echo "Ошибка: jq не установлен. Пожалуйста, установите jq для обработки JSON."
    exit 1
fi

# Проверяем статус репозитория и добавляем изменения
git add .
check_command "Не удалось выполнить 'git add'" "Изменения добавлены в индекс."

# Получаем изменения
git_diff=$(git diff --cached)

# Логируем diff
echo "Git diff:"
echo "$git_diff"

# Проверяем количество символов в diff
diff_length=${#git_diff}
max_diff_length=50000  # Максимальное количество символов (можно настроить)

# Если нет изменений, завершаем скрипт
if [ -z "$git_diff" ]; then
    echo "Нет изменений для коммита. Скрипт завершен."
    exit 0
fi

# Если diff слишком большой, используем стандартное сообщение
if [ $diff_length -gt $max_diff_length ]; then
    commit_description="слишком много изменений"
    echo "Diff слишком большой ($diff_length символов). Используем стандартное описание."
else
    # Логируем запрос к API
    echo "Отправляем запрос к API OpenAI..."

    # Генерируем описание коммита с помощью GPT-4
    api_response=$(curl -s https://api.openai.com/v1/chat/completions \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $OPENAI_API_KEY" \
      -d @<(cat <<EOF
    {
      "model": "gpt-4o-mini",
      "messages": [
        {
          "role": "system",
          "content": "Вы - опытный разработчик, пишущий краткие, но информативные описания коммитов. Опишите изменения в коде на основе diff, используя не более 70 слов."
        },
        {
          "role": "user",
          "content": $(jq -Rs . <<< "$git_diff")
        }
      ],
      "max_tokens": 100
    }
EOF
    ))

    # Логируем ответ API
    echo "Ответ API:"
    echo "$api_response"

    # Проверяем наличие ошибки в ответе API
    if echo "$api_response" | jq -e '.error' > /dev/null; then
        error_message=$(echo "$api_response" | jq -r '.error.message')
        commit_description="слишком много изменений"
        echo "Ошибка API: $error_message"
    else
        # Извлекаем описание коммита из ответа API
        commit_description=$(echo "$api_response" | jq -r '.choices[0].message.content')
    fi
fi

# Логируем извлеченное описание
echo "Извлеченное описание коммита:"
echo "$commit_description"

# Коммитим изменения с сгенерированным описанием
git commit -m "$commit_description"
check_command "Не удалось выполнить коммит"

# Пушим изменения в удалённый репозиторий
git push origin main
if [ $? -ne 0 ]; then
    echo "Push не удался. Пробуем сначала выполнить pull."
    git pull --rebase origin main
    check_command "Не удалось выполнить 'git pull'" "Pull выполнен успешно."
    
    git push origin main
    check_command "Не удалось выполнить 'git push' после pull" "Изменения отправлены в удалённый репозиторий."
else
    echo "Изменения отправлены в удалённый репозиторий."
fi

# Проверяем, установлен ли sshpass
if ! command -v sshpass &> /dev/null; then
    echo "Ошибка: sshpass не установлен."
    exit 1
fi

# Выполняем pull на сервере после успешного пуша
sshpass -p "$VPS_PASSWORD" ssh -o StrictHostKeyChecking=no root@"$VPS_IP" "
    cd $DIRECTORY || exit
    echo 'Сброс локальных изменений и обновление с удаленного репозитория...'
    git fetch origin main
    git reset --hard origin/main
    git clean -fd
    
    # Rebuild and restart Docker containers
    docker-compose down
    docker-compose build --no-cache
    docker-compose up -d
    
    # Restart Nginx
    systemctl restart nginx
"

# Additional changes for backend dependencies
cd src/backend
pip install -r requirements.txt
cd ../..

# Ensure we're in the git repository root
cd "$(git rev-parse --show-toplevel)" || exit 1

# Get and display the full commit hash
commit_hash=$(git rev-parse HEAD)
echo "Полный хэш коммита: $commit_hash"

# Output commit description
echo "Описание коммита:"
echo "$commit_description"

# Сообщение об успешном завершении скрипта
echo "Всё успешно выполнено!"

# Добавляем вывод сообщения в нужном формате
echo -e "\n\"\"\"" 
echo "$commit_description"
echo
echo "\`\`\`$commit_hash\`\`\`"
echo
commit_keywords="коммиты, общие знания, география, математика, история"
echo "$commit_keywords"
echo "\"\"\""

