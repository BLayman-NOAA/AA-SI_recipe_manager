/** EchogramGL's decode worker, as Vite serves and builds it from the package. */
import workerUrl from 'aa-si-echogram-gl/decode-worker?url';

export const spawnDecodeWorker = () => new Worker(workerUrl, { type: 'module' });
