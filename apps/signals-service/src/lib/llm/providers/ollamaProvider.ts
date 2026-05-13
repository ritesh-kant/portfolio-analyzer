import axios from 'axios';

interface OllamaMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

interface OllamaChatResponse {
  message: OllamaMessage;
}

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const baseUrl = process.env.OLLAMA_BASE_URL ?? 'http://localhost:11434';
  const model = process.env.OLLAMA_MODEL ?? 'llama3.1';
  const timeout = parseInt(process.env.OLLAMA_TIMEOUT ?? '600000', 10); // default 10 min

  const response = await axios.post<OllamaChatResponse>(
    `${baseUrl}/api/chat`,
    {
      model,
      stream: false,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
    },
    { timeout },
  );

  return response.data.message.content;
}
