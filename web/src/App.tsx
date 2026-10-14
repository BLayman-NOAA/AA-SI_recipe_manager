import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Shell } from './shell/Shell';
import { useView } from './state/view';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { refetchOnWindowFocus: false, retry: 1 },
    },
  });
}

/** Keep the theme attribute on <html> in step with the view state. */
function useThemeAttribute() {
  const theme = useView((state) => state.theme);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
}

export function App({ client }: { client?: QueryClient }) {
  const [queryClient] = useState(() => client ?? createQueryClient());
  useThemeAttribute();
  return (
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  );
}
