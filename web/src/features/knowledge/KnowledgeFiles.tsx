import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../api/client";
import * as api from "../../api/knowledgeFiles";
import { listCatalog, type Workspace } from "../../api/operations";
import { Paging } from "../content/shared";
import { time } from "../content/hooks";
import { FileInputError, prepareFile } from "./upload";

const states: Record<string, string> = {
  queued: "В очереди",
  processing: "Проверка и извлечение",
  ready: "Готов к проверке владельцем",
  failed: "Обработка не завершена",
  cancelled: "Отменён",
};
const active = (state?: string) => state === "queued" || state === "processing";

function FileFailure({ error }: { error: Error }) {
  const code =
    error instanceof ApiError
      ? error.code
      : error instanceof FileInputError
        ? error.message
        : "request_unconfirmed";
  const messages: Record<string, string> = {
    binary_ingestion_disabled:
      "Загрузка на сервере ещё не включена. Администратору нужно проверить сканер и изолированную обработку.",
    file_size_invalid: "Выберите непустой файл размером не больше 2 МиБ.",
    invalid_filename:
      "Имя файла должно быть не длиннее 160 символов, без служебных знаков и путей.",
    file_type_mismatch:
      "Поддерживаются PDF, DOCX, Markdown, CSV и HTML. Расширение должно соответствовать содержимому.",
    text_encoding_invalid:
      "Текстовые файлы принимаются только в UTF-8. Пересохраните файл без потери символов.",
    text_controls_rejected:
      "В тексте обнаружены двоичные или управляющие символы.",
    extracted_text_empty: "В файле нет доступного текста.",
    secure_context_required:
      "Для чтения файла нужен современный браузер и защищённый HTTPS-адрес панели.",
    file_storage_quota_exceeded:
      "Лимит файлов исчерпан. Обратитесь к администратору; отмена не освобождает место.",
    pending_request_changed:
      "Исход предыдущей загрузки неизвестен. Верните тот же файл, имя и бренд и повторите отправку для сверки с сервером.",
    ingestion_conflict:
      "Версия задания изменилась. Обновите данные и решите, нужна ли отмена новой версии.",
    ingestion_cancel_not_allowed:
      "Задание уже нельзя отменить. Обновите данные, чтобы увидеть результат.",
    access_denied:
      "Доступ изменился. Войдите заново под своей учётной записью.",
    not_found: "Файл недоступен в текущем пространстве и с текущими правами.",
    invalid_request: "Проверьте выбранный бренд, файл и его имя.",
    request_too_large:
      "Запрос слишком большой. Допустимый размер файла — 2 МиБ.",
  };
  return (
    <div className="inline-error" role="alert">
      <p>
        {messages[code] ??
          "Сервер не подтвердил результат. Проверьте связь и повторите то же действие. Для загрузки выберите тот же файл, имя и бренд: повтор не создаст дубликат."}
      </p>
      {error instanceof ApiError && (
        <small>
          {code} · {error.correlation}
        </small>
      )}
    </div>
  );
}

// No mutation cache: file bytes and base64 live only in the in-flight request closure.
function useRequest() {
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function run<T>(
    execute: (signal: AbortSignal) => Promise<T>,
    accept: (result: T) => void,
  ) {
    if (controller.current) return;
    const request = new AbortController();
    controller.current = request;
    setBusy(true);
    setError(null);
    try {
      const result = await execute(request.signal);
      if (!request.signal.aborted) accept(result);
    } catch (caught) {
      if (!request.signal.aborted)
        setError(
          caught instanceof Error ? caught : new Error("request_unconfirmed"),
        );
    } finally {
      controller.current = null;
      if (!request.signal.aborted) setBusy(false);
    }
  }
  return { run, busy, error };
}

export function KnowledgeFiles({
  workspace,
  offline,
}: {
  workspace: Workspace;
  offline: boolean;
}) {
  // Also gate direct component use; the server still authorizes every request.
  if (!workspace.permissions.includes("knowledge.write"))
    return <p>Нет доступа к загрузке файлов.</p>;
  return <Files key={workspace.id} workspace={workspace} offline={offline} />;
}

function Files({
  workspace,
  offline,
}: {
  workspace: Workspace;
  offline: boolean;
}) {
  const [cursor, setCursor] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const query = useQuery({
    queryKey: [workspace.id, "files", cursor],
    queryFn: ({ signal }) => api.files(workspace.id, cursor, signal),
    enabled: !offline,
    gcTime: 0,
    retry: false,
    refetchInterval: (q) =>
      !offline && q.state.data?.items.some((f) => active(f.state))
        ? 5000
        : false,
  });
  return (
    <section aria-labelledby="files-heading">
      <h2 id="files-heading">Файлы для базы знаний</h2>
      <p>
        Сначала проверка файла на сервере. Добавление в знания и подтверждение
        фактов — отдельные решения владельца.
      </p>
      <Upload
        workspace={workspace}
        offline={offline}
        onUploaded={(id) => {
          setCursor(undefined);
          setSelected(id);
        }}
      />
      <div className="file-list-heading">
        <h3>Доступные файлы</h3>
        <button
          disabled={offline || query.isFetching}
          onClick={() => void query.refetch()}
        >
          Обновить список
        </button>
      </div>
      {query.isPending && (
        <p role="status">
          {offline ? "Список недоступен без связи." : "Загрузка списка…"}
        </p>
      )}
      {query.error && <FileFailure error={query.error} />}
      {query.isFetching && query.data && (
        <p role="status">Обновление списка…</p>
      )}
      {query.data?.items.length === 0 && (
        <p>Файлов пока нет. Выберите бренд и загрузите первый документ.</p>
      )}
      <ul className="knowledge-list file-list">
        {query.data?.items.map((f) => (
          <li key={f.id}>
            <div>
              <button
                aria-expanded={selected === f.id}
                aria-controls="file-inspector"
                onClick={() => setSelected(f.id)}
              >
                {f.filename}
              </button>
              <p>
                {f.format.toUpperCase()} ·{" "}
                {new Intl.NumberFormat("ru").format(f.byte_size)} байт ·{" "}
                {time(f.created_at, workspace.timezone)}
              </p>
            </div>
            <span>{states[f.state] ?? "Неизвестный статус"}</span>
          </li>
        ))}
      </ul>
      {query.data && <Paging next={query.data.next_cursor} set={setCursor} />}
      <div id="file-inspector">
        {selected && (
          <FileInspector
            key={selected}
            id={selected}
            workspace={workspace}
            offline={offline}
          />
        )}
      </div>
    </section>
  );
}

function Upload({
  workspace,
  offline,
  onUploaded,
}: {
  workspace: Workspace;
  offline: boolean;
  onUploaded: (id: string) => void;
}) {
  const cache = useQueryClient();
  const [brandCursor, setBrandCursor] = useState<string>();
  const [brand, setBrand] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [receipt, setReceipt] = useState<string>();
  const pendingKey = useRef<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const request = useRequest();
  const brands = useQuery({
    queryKey: [workspace.id, "catalog", "brands", brandCursor],
    queryFn: ({ signal }) =>
      listCatalog(workspace.id, "brands", brandCursor, signal),
    enabled: !offline,
    retry: false,
  });
  return (
    <form
      className="file-upload"
      onSubmit={(event) => {
        event.preventDefault();
        if (!file || !brand || offline) return;
        setReceipt(undefined);
        void request.run(
          async (signal) => {
            const body = await prepareFile(file, workspace.id, brand);
            signal.throwIfAborted();
            if (
              pendingKey.current &&
              pendingKey.current !== body.idempotency_key
            )
              throw new FileInputError("pending_request_changed");
            pendingKey.current = body.idempotency_key;
            try {
              return await api.submit(workspace.id, body, signal);
            } catch (error) {
              if (
                error instanceof ApiError &&
                (error.status < 500 ||
                  error.code === "binary_ingestion_disabled")
              )
                pendingKey.current = null;
              throw error;
            }
          },
          (result) => {
            pendingKey.current = null;
            if (fileInput.current) fileInput.current.value = "";
            setFile(null);
            setReceipt(result.file_id);
            onUploaded(result.file_id);
            void cache.invalidateQueries({ queryKey: [workspace.id, "files"] });
          },
        );
      }}
    >
      <fieldset disabled={offline || request.busy}>
        <legend>Загрузить документ</legend>
        <p id="file-limits">
          PDF, DOCX, Markdown, CSV или HTML, до 2 МиБ. Текстовые файлы — только
          UTF-8; CSV с запятыми и заголовком, HTML без активного содержимого.
          CSV загружается как справочный текст, не продажи или метрики. Без OCR.
          Не загружайте секреты.
        </p>
        <div className="file-fields">
          <label>
            Бренд
            <select
              required
              value={brand}
              disabled={!brands.data || Boolean(brands.error)}
              onChange={(e) => setBrand(e.target.value)}
            >
              <option value="">Выберите бренд</option>
              {brands.data?.items.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Документ
            <input
              ref={fileInput}
              type="file"
              required
              accept=".pdf,.docx,.md,.markdown,.csv,.html,.htm"
              aria-describedby="file-limits"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button
            className="primary"
            disabled={!file || !brand || Boolean(brands.error)}
            type="submit"
          >
            {request.busy ? "Отправка…" : "Загрузить файл"}
          </button>
        </div>
        {brands.isPending && (
          <p role="status">
            {offline ? "Загрузка недоступна без связи." : "Загрузка брендов…"}
          </p>
        )}
        {brands.data?.items.length === 0 && (
          <p>Брендов нет. Сначала создайте бренд через чат.</p>
        )}
        {brands.error && (
          <>
            <FileFailure error={brands.error} />
            <button type="button" onClick={() => void brands.refetch()}>
              Обновить бренды
            </button>
          </>
        )}
        {(brandCursor || brands.data?.next_cursor) && (
          <div className="file-brand-pages">
            <p>Не нашли бренд? Откройте следующую страницу списка.</p>
            <Paging
              next={brands.data?.next_cursor}
              set={(next) => {
                setBrand("");
                setBrandCursor(next);
              }}
            />
          </div>
        )}
      </fieldset>
      {request.busy && (
        <p role="status">
          Отправляем файл. Это ещё не подтверждение обработки.
        </p>
      )}
      {request.error && <FileFailure error={request.error} />}
      {receipt && (
        <p role="status">
          Сервер подтвердил загрузку: <code>{receipt}</code>. Результат проверки
          — ниже.
        </p>
      )}
      <p className="muted">
        Повтор того же файла, имени и бренда вернёт прежнюю запись. Для
        повторной проверки используйте rescan через чат. Файлы не сохраняются в
        постоянном хранилище браузера.
      </p>
    </form>
  );
}

function FileInspector({
  id,
  workspace,
  offline,
}: {
  id: string;
  workspace: Workspace;
  offline: boolean;
}) {
  const cache = useQueryClient();
  const request = useRequest();
  const [pendingCancel, setPendingCancel] = useState<api.CancelFile | null>(
    null,
  );
  const [cancelledVersion, setCancelledVersion] = useState<number>();
  const query = useQuery({
    queryKey: [workspace.id, "file", id],
    queryFn: ({ signal }) => api.file(workspace.id, id, signal),
    enabled: !offline,
    gcTime: 0,
    retry: false,
    refetchInterval: (q) =>
      !offline && active(q.state.data?.state) ? 5000 : false,
  });
  const history = useQuery({
    queryKey: [workspace.id, "file-history", id, query.data?.version],
    queryFn: ({ signal }) => api.history(workspace.id, id, signal),
    enabled: !offline && Boolean(query.data),
    gcTime: 0,
    retry: false,
  });
  function refresh() {
    void query.refetch();
    void history.refetch();
  }
  const f = query.data;
  return (
    <section
      className="knowledge-detail file-detail"
      aria-labelledby="file-detail-heading"
    >
      <div className="file-list-heading">
        <h3 id="file-detail-heading">{f?.filename ?? "Сведения о файле"}</h3>
        <button
          disabled={offline || query.isFetching || history.isFetching}
          onClick={refresh}
        >
          Обновить файл
        </button>
      </div>
      {query.isPending && (
        <p role="status">
          {offline
            ? "Сведения недоступны без связи."
            : "Проверяем состояние файла…"}
        </p>
      )}
      {query.error && <FileFailure error={query.error} />}
      {f && (
        <>
          <p role="status">
            {states[f.state] ?? "Неизвестный статус"} · версия {f.version} ·
            попыток: {f.attempts}
          </p>
          {f.error_code && (
            <p className="eval-notice">
              Обработка остановлена: <code>{f.error_code}</code>. Уточните
              причину через чат по ID файла; автоматического повтора нет.
            </p>
          )}
          <dl className="file-metadata">
            <dt>ID файла</dt>
            <dd>
              <code>{f.id}</code>
            </dd>
            <dt>SHA-256 оригинала</dt>
            <dd>
              <code>{f.content_hash}</code>
            </dd>
          </dl>
          {(active(f.state) || pendingCancel) && (
            <div className="file-cancel">
              <p>
                Отмена запрещает сохранение результата обработки. Она не удаляет
                оригинал, не освобождает квоту и может не остановить уже
                запущенный сканер.
              </p>
              <button
                disabled={offline || request.busy || Boolean(query.error)}
                onClick={() => {
                  const body = pendingCancel ?? {
                    kind: "file" as const,
                    job_id: id,
                    expected_version: f.version,
                    idempotency_key: `browser-file-cancel-v1:${id}:${f.version}`,
                  };
                  setPendingCancel(body);
                  void request.run(
                    async (signal) => {
                      try {
                        return await api.cancel(workspace.id, body, signal);
                      } catch (error) {
                        if (
                          !signal.aborted &&
                          error instanceof ApiError &&
                          error.status < 500
                        )
                          setPendingCancel(null);
                        throw error;
                      }
                    },
                    (result) => {
                      setPendingCancel(null);
                      setCancelledVersion(result.version);
                      refresh();
                      void cache.invalidateQueries({
                        queryKey: [workspace.id, "files"],
                      });
                    },
                  );
                }}
              >
                {request.busy
                  ? "Запрашиваем отмену…"
                  : pendingCancel
                    ? "Повторить отмену"
                    : "Отменить обработку"}
              </button>
            </div>
          )}
          {f.extraction && (
            <>
              <p className="eval-notice">
                Извлечённый текст не проверен владельцем и не является
                действующим знанием или подтверждённым фактом. Антивирусная
                проверка не гарантирует безопасность файла.
              </p>
              <dl className="file-metadata">
                <dt>SHA-256 текста</dt>
                <dd>
                  <code>{f.extraction.text_hash}</code>
                </dd>
                <dt>Парсер</dt>
                <dd>{f.extraction.parser_version}</dd>
                <dt>Сканер и сигнатуры</dt>
                <dd>
                  {f.extraction.scan_engine} · {f.extraction.signature_version}
                </dd>
                <dt>Сигнатуры обновлены</dt>
                <dd>
                  {time(f.extraction.signatures_updated_at, workspace.timezone)}
                </dd>
                <dt>Проверка выполнена</dt>
                <dd>{time(f.extraction.scanned_at, workspace.timezone)}</dd>
              </dl>
              <details>
                <summary>Прочитать извлечённый текст</summary>
                <pre className="file-extraction">{f.extraction.text}</pre>
              </details>
              <p>
                Владелец может проверить текст через чат по ID файла и SHA-256
                текста. Импорт и включение индекса выполняются отдельно; эта
                панель их не запускает.
              </p>
            </>
          )}
        </>
      )}
      {request.error && <FileFailure error={request.error} />}
      {cancelledVersion !== undefined && (
        <p role="status">
          Сервер подтвердил отмену, версия {cancelledVersion}.
        </p>
      )}
      <h4>История обработки</h4>
      {history.isPending && f && (
        <p role="status">
          {offline ? "История недоступна без связи." : "Загрузка истории…"}
        </p>
      )}
      {history.error && <FileFailure error={history.error} />}
      {history.data?.events.length === 0 && <p>События ещё не записаны.</p>}
      <ol className="file-history">
        {history.data?.events.map((event) => (
          <li key={event.version}>
            <p>
              {states[event.state] ?? "Неизвестный статус"} · версия{" "}
              {event.version} · попыток: {event.attempts}
            </p>
            <small>
              {time(event.created_at, workspace.timezone)} ·{" "}
              {event.actor_id ? `Пользователь ${event.actor_id}` : "Система"}
              {event.error_code && ` · ${event.error_code}`}
            </small>
          </li>
        ))}
      </ol>
      {history.data?.truncated && (
        <p>
          Показаны последние 50 событий. Более ранняя история здесь не
          отображается.
        </p>
      )}
    </section>
  );
}
