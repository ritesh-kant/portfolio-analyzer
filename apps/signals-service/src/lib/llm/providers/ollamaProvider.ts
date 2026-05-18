import axios from 'axios';
import { config } from '../../config.js';

interface OllamaMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

interface OllamaChatResponse {
  message: OllamaMessage;
}

export async function call(systemPrompt: string, userPrompt: string): Promise<string> {
  const baseUrl = config.ollamaBaseUrl;
  const model = config.ollamaModel;
  const timeout = config.ollamaTimeout;

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
