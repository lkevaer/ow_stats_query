import { AppProvider } from '@/store/appContext';
import './app.scss';

function App({ children }: { children: React.ReactNode }) {
  return <AppProvider>{children}</AppProvider>;
}

export default App;