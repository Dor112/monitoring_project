define(["react", "@grafana/data", "@grafana/runtime"], function (react, data, runtime) {
  const React = react.default || react;
  const h = React.createElement;
  const { AppPlugin } = data;
  const { getBackendSrv } = runtime;
  const colors = { blue: "#73a8fc", green: "#63c995", yellow: "#f1ba59", red: "#f17881", purple: "#b69af4" };
  const severity = {
    normal: ["Штатно", colors.green],
    warning: ["Предаварийное", colors.yellow],
    critical: ["Критическое по модели", colors.red],
  };
  const names = {
    "CPU busy %": ["Загрузка CPU", "%", colors.blue],
    "CPU iowait %": ["Ожидание ввода-вывода", "%", colors.purple],
    "RAM used %": ["Занято RAM", "%", colors.green],
    "Disk IOPS": ["Дисковые операции", "оп/с", colors.yellow],
    "Network RX bytes/s": ["Входящий трафик", "КиБ/с", colors.blue, 1024],
    "Network TX bytes/s": ["Исходящий трафик", "КиБ/с", colors.purple, 1024],
  };
  const mainSeries = [
    ["score", "Общий индекс", colors.green],
    ["forest", "Isolation Forest", colors.purple],
    ["autoencoder", "GRU", colors.blue],
  ];
  const liveGroups = [
    ["Процессор", ["CPU busy %", "CPU iowait %"]],
    ["Дисковая подсистема", ["Disk IOPS"]],
    ["Оперативная память", ["RAM used %"]],
    ["Сетевой трафик", ["Network RX bytes/s", "Network TX bytes/s"]],
  ];
  const styles = `
    .nirs { max-width: 1600px; margin: auto; padding: clamp(12px, 2vw, 24px); color: #dce2eb; background: #111217; font-family: Inter, system-ui, sans-serif; }
    .nirs h1 { font-size: 27px; margin: 0 0 6px; }
    .nirs h2 { font-size: 16px; margin: 0 0 12px; }
    .nirs p { margin: 6px 0; }
    .nirs-header, .nirs-line { display: flex; align-items: center; flex-wrap: wrap; justify-content: space-between; gap: 12px; }
    .nirs-header { margin-bottom: 20px; }
    .nirs-muted { color: #a5b0c2; font-size: 13px; }
    .nirs-controls { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .nirs button, .nirs select { color: inherit; background: #202631; border: 1px solid #424c5d; border-radius: 5px; padding: 7px 11px; }
    .nirs button[aria-pressed=true] { border-color: #73a8fc; background: #223452; }
    .nirs button:focus-visible, .nirs select:focus-visible, .nirs input:focus-visible { outline: 2px solid #8cb8ff; outline-offset: 2px; }
    .nirs-panel { min-width: 0; background: #181b22; border: 1px solid #2b313c; border-radius: 7px; padding: 15px; }
    .nirs-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 235px), 1fr)); gap: 12px; }
    .nirs-stack { display: grid; gap: 12px; }
    .nirs-console { margin-bottom: 14px; }
    .nirs-console .nirs-panel { background: #111217; border-radius: 4px; }
    .nirs-console h2 { font-size: 13px; font-weight: 500; color: #aebbd0; text-transform: uppercase; }
    .nirs-console-title { border-left: 4px solid #ff8a38; padding-left: 10px; font-weight: 600; }
    .nirs-banner { padding: 12px 14px; border: 1px solid; border-radius: 4px; margin: 0 0 14px; }
    .nirs-banner strong { font-size: 16px; }
    .nirs-scale { position: relative; overflow: hidden; height: 13px; background: #242932; border: 1px solid #37404b; border-radius: 4px; margin: 6px 0; }
    .nirs-scale-fill { height: 100%; }
    .nirs-scale-marker { position: absolute; top: 0; bottom: 0; width: 2px; }
    .nirs-readouts { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 140px), 1fr)); gap: 8px; margin-top: 12px; }
    .nirs-readouts > div { padding: 8px 10px; background: #191d22; border: 1px solid #2b313c; border-radius: 4px; }
    .nirs-mono { font-family: "IBM Plex Mono", Consolas, monospace; font-variant-numeric: tabular-nums; }
    .nirs-log { list-style: none; max-height: 175px; overflow: auto; background: #0e1013; padding: 10px; margin: 0; border: 1px solid #2b313c; border-radius: 4px; }
    .nirs-log li + li { margin-top: 5px; }
    .nirs-group svg, .nirs-history svg { max-height: 245px; }
    .nirs-number { font-size: 23px; font-variant-numeric: tabular-nums; font-weight: 600; }
    .nirs-legend { display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 12px; margin: 10px 0; }
    .nirs svg { width: 100%; display: block; overflow: visible; }
    .nirs table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
    .nirs th, .nirs td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #303746; }
    .nirs progress { display: block; width: 100%; margin-top: 5px; height: 8px; accent-color: #73a8fc; }
    .nirs input[type=range] { width: 100%; accent-color: #73a8fc; }
    .nirs-footer { margin-top: 16px; }
    .nirs-alert { color: #f6b2b8; padding: 10px 12px; border: 1px solid #83424a; margin-bottom: 16px; }
  `;
  const format = (n, digits = 2) => Number.isFinite(n) ? n.toLocaleString("ru-RU", {
    maximumFractionDigits: digits, minimumFractionDigits: digits,
  }) : "—";
  const clock = (step) => new Date(step * 1000).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const caption = (step, live) => live ? clock(step) : "#" + step;
  const minuteRanges = [30, 60, 240];
  const sampleRanges = [60, 120, 240];

  function mergeHistory(previous, incoming) {
    if (!incoming.length) return previous;
    const history = new Map(previous.map((point) => [point.step, point]));
    for (const point of incoming) history.set(point.step, point);
    return [...history.values()].sort((a, b) => a.step - b.step).slice(-240);
  }

  function statusText(status) {
    const baseline = /^baseline: (\d+)\/240/.exec(status || "");
    if (baseline) return "Сбор базы · " + baseline[1] + " / 240 мин";
    if (status === "starting") return "Запуск";
    if (status === "training") return "Обучение модели";
    if (status === "running") return "Данные поступают";
    if (status === "replay completed") return "Воспроизведение завершено";
    if (status === "missing or invalid telemetry") return "Есть пропуски в телеметрии";
    if (status?.startsWith("training on ")) return "Обучение · " + status.slice(12);
    return status || "Загрузка";
  }

  function Graph({ points, series, live, selected, thresholds = [], domain, compact = false, sampleJitter = 0, events = [] }) {
    if (!points.some((point) => series.some(({ read }) => Number.isFinite(read(point))))) {
      return h("p", { className: "nirs-muted" }, "Нет данных за выбранный интервал");
    }
    const values = points.flatMap((point) => series.map(({ read }) => read(point)).filter(Number.isFinite));
    const low = domain ? domain[0] : Math.min(0, ...values);
    const high = domain ? domain[1] : Math.max(low + 0.001, ...values);
    const w = compact ? 330 : 820;
    const height = compact ? 108 : 222;
    const left = compact ? 47 : 48;
    const right = w - 15;
    const bottom = height - 23;
    const first = points[0].step;
    const last = points.at(-1).step;
    const x = (step) => left + (right - left) * (step - first) / Math.max(last - first, 1);
    const y = (value) => bottom - (bottom - 9) * (value - low) / (high - low);
    const elements = [];
    for (const fraction of [0, 0.5, 1]) {
      const value = low + fraction * (high - low);
      elements.push(h("line", { key: "grid" + fraction, x1: left, x2: right, y1: y(value), y2: y(value), stroke: "#313949" }));
      elements.push(h("text", { key: "axis" + fraction, x: left - 5, y: y(value) + 4, textAnchor: "end" },
        format(value, domain ? 1 : high < 10 ? 2 : high < 100 ? 1 : 0)));
    }
    for (const [name, value, color] of thresholds) {
      if (Number.isFinite(value)) elements.push(h("line", { key: name, x1: left, x2: right, y1: y(value), y2: y(value), stroke: color, strokeDasharray: "5 4" }));
    }
    for (const { time, status, message } of events) {
      if (time < first || time > last) continue;
      elements.push(h("line", { key: status + time, x1: x(time), x2: x(time), y1: 9, y2: bottom,
        stroke: status === "restart" ? colors.red : colors.green, strokeDasharray: "4 4" }, h("title", null, message)));
    }
    for (const { key, read, color } of series) {
      let path = "";
      let prior = null;
      for (const point of points) {
        const value = read(point);
        if (!Number.isFinite(value)) {
          prior = null;
          continue;
        }
        const contiguous = prior !== null && Math.abs(point.step - prior - (live ? 60 : 1)) <= sampleJitter;
        path += (contiguous ? " L" : " M") + x(point.step) + " " + y(value);
        prior = point.step;
      }
      elements.push(h("path", { key, d: path, stroke: color, strokeWidth: compact ? 1.5 : 2, fill: "none" }));
      const value = selected && read(selected);
      if (Number.isFinite(value)) elements.push(h("circle", { key: key + "-value", cx: x(selected.step), cy: y(value), r: 3, fill: color }));
    }
    if (!compact && !live) for (const point of points) {
      if (point.label) elements.push(h("circle", { key: "label" + point.step, cx: x(point.step), cy: bottom + 4, r: 2, fill: colors.red }));
    }
    const tickCount = Math.min(compact ? 2 : 7, points.length);
    for (let index = 0; index < tickCount; index++) {
      const offset = index / Math.max(tickCount - 1, 1);
      const { step } = points[Math.round(offset * (points.length - 1))];
      const anchor = index === 0 ? "start" : index === tickCount - 1 ? "end" : "middle";
      elements.push(h("text", { key: "time" + index, x: x(step), y: height - 3, textAnchor: anchor }, caption(step, live)));
    }
    return h("svg", { role: "img", "aria-label": series.map((item) => item.name).join(", "),
      viewBox: "0 0 " + w + " " + height, fill: "#a5b0c2", fontSize: 11 }, elements);
  }

  function Metric({ metric, points, selected, live }) {
    const [name, unit, color, scale = 1] = names[metric] || [metric, "", colors.blue];
    const read = (point) => Number.isFinite(point.values?.[metric]) ? point.values[metric] / scale : null;
    return h("article", { className: "nirs-panel" },
      h("h2", null, name),
      h("div", { className: "nirs-number" }, format(selected && read(selected), !live ? 4 : 2), " ",
        h("span", { className: "nirs-muted" }, unit)),
      h(Graph, { points, series: [{ key: metric, name, color, read }], live, selected,
        compact: true, domain: unit === "%" ? [0, 100] : undefined }));
  }

  function LiveGroup({ title, metrics, points, selected }) {
    const series = metrics.map((key) => {
      const [name, unit, color, scale = 1] = names[key];
      const read = (point) => {
        const value = point?.values?.[key];
        return Number.isFinite(value) ? value / scale : null;
      };
      return { key, name, unit, color, read };
    });
    return h("article", { className: "nirs-panel nirs-group" },
      h("h2", null, title),
      h(Graph, { points, selected, series, live: true, domain: series[0].unit === "%" ? [0, 100] : undefined }),
      h("div", { className: "nirs-legend nirs-mono" }, ...series.map(({ key, name, unit, color, read }) =>
        h("span", { key, style: { color } }, name + " · " + format(read(selected)) + " " + unit))));
  }

  function Benchmark({ state }) {
    const result = state.benchmark;
    return h("section", { className: "nirs-panel nirs-footer" },
      h("h2", null, "Оценка моделей · " + state.machine),
      h("table", null,
        h("thead", null, h("tr", null, ...["Модель", "Precision", "Recall", "F1"].map((item) => h("th", { key: item, scope: "col" }, item)))),
        h("tbody", null, ...["Isolation Forest", "GRU", "Combined"].map((key) =>
          h("tr", { key }, h("th", { scope: "row" }, key === "Combined" ? "Комбинация" : key),
            ...["precision", "recall", "f1"].map((field) => h("td", { key: field }, format(result[key][field], 3))))))),
      h("p", { className: "nirs-muted" }, result.points + " отсчётов · " + format(result.batch_ms_per_point, 3) + " мс/отсчёт при пакетном расчёте"));
  }

  function MemoryGuard({ guard, lab = false }) {
    const points = (guard.history || guard.samples || []).map(([step, bytes]) => ({ step, value: bytes / 1048576 }));
    const events = guard.events || [];
    const statuses = {
      normal: "Штатно", high: "Выше 85% бюджета", sustained: "Длительный рост памяти",
      restarting: "Перезапуск", recovered: "Восстановлен", latched: "Нужна проверка администратора",
      unavailable: "Измерение недоступно",
    };
    const limit = guard.limit == null ? null : guard.limit / 1048576;
    const series = [{ key: "memory", name: "Память сервиса, МиБ", color: colors.green, read: (point) => point.value }];
    const thresholds = Number.isFinite(limit) ? [["85% бюджета", limit * 0.85, colors.yellow]] : [];
    const max = Math.max(1, limit || 0, ...points.map((point) => point.value));
    return h("section", { className: "nirs-panel nirs-footer nirs-group" },
      h("h2", null, lab ? "Стендовый опыт · автоматический перезапуск по RAM" : "Защита памяти сервиса"),
      h("p", null, guard.target + " · " + (statuses[guard.status] || guard.status)),
      h("p", { className: "nirs-muted" },
        (guard.mode === "restart" ? "Автоматический перезапуск" : "Наблюдение") +
        " · Память " + format(points.at(-1)?.value, 1) + " / " + format(limit, 1) + " МиБ"),
      h(Graph, { points, series, selected: points.at(-1), live: true, thresholds, domain: [0, max], sampleJitter: 60,
        events: events.filter((item) => ["restart", "recovered"].includes(item.status)) }),
      h("p", { className: "nirs-muted" }, "Жёлтая линия — 85% бюджета · красная отметка — начало перезапуска · зелёная — подтверждение восстановления"),
      h("ul", { className: "nirs-log nirs-mono" },
        ...events.slice().reverse().map((item, index) =>
          h("li", { key: item.time + "-" + index }, new Date(item.time * 1000).toLocaleString("ru-RU") + " · " + item.message))));
  }

  function Root() {
    const [mode, setMode] = React.useState("live");
    const [snapshot, setSnapshot] = React.useState(null);
    const [error, setError] = React.useState("");
    const [range, setRange] = React.useState(240);
    const [inspection, setInspection] = React.useState(null);
    React.useEffect(() => {
      let active = true;
      let timer;
      let current = null;
      setError("");
      async function load() {
        let delay = mode === "live" ? 15000 : 2000;
        try {
          const params = new URLSearchParams({ mode });
          if (current?.stream) {
            params.set("stream", current.stream);
            params.set("after", current[mode].points.at(-1)?.step ?? -1);
            params.set("after_telemetry", current[mode].telemetry?.at(-1)?.step ?? -1);
          }
          const next = await getBackendSrv().get("api/plugin-proxy/nirs-servermonitor-app/monitor/status?" + params);
          if (!active) return;
          const state = next[mode];
          if (!state) throw new Error("В ответе нет выбранного режима");
          if (next.stream && current?.stream === next.stream) {
            const prior = current[mode];
            state.points = mergeHistory(prior.points, state.points);
            if (state.telemetry) state.telemetry = mergeHistory(prior.telemetry || [], state.telemetry);
          }
          current = { [mode]: state, stream: next.stream, actions: next.actions, action_mode: next.action_mode };
          if (state.status === "replay completed") delay = 30000;
          setSnapshot(current);
          setError("");
        } catch (failure) {
          if (active) setError(failure.message || "Сервис анализа недоступен");
          delay = 15000;
        } finally {
          if (active) timer = setTimeout(load, delay);
        }
      }
      load();
      return () => { active = false; clearTimeout(timer); };
    }, [mode]);

    const live = mode === "live";
    const state = snapshot?.[mode] || {};
    const telemetry = state.telemetry || state.points || [];
    const latest = telemetry.at(-1);
    const firstStep = latest ? latest.step - (range - 1) * (live ? 60 : 1) : 0;
    const history = telemetry.filter((point) => point.step >= firstStep);
    const recentScores = (state.points || []).filter((point) => point.step >= firstStep);
    const selected = history.find((point) => point.step === inspection) || latest;
    const point = recentScores.find((sample) => sample.step === selected?.step);
    const stale = live && latest && (error || Date.now() / 1000 - latest.step > 150);
    const hasMissing = selected?.values && Object.values(selected.values).some((value) => !Number.isFinite(value));
    const [stateLabel, stateColor] = stale ? ["Телеметрия устарела", colors.yellow] :
      severity[point?.state] || [hasMissing ? "Пропуск телеметрии" : "Оценка недоступна", colors.yellow];
    const metricNames = state.metric_names || Object.keys(latest?.values || {});
    const criticalCount = recentScores.filter((sample) => sample.state === "critical").length;
    const thresholds = [
      ["Порог предупреждения", state.warning, colors.yellow],
      ["Критический порог", state.critical, colors.red],
    ];
    const events = (snapshot?.actions || []).filter((event) => event.mode === mode).slice().reverse();
    return h("main", { className: "nirs" },
      h("style", null, styles),
      h("header", { className: "nirs-header" },
        h("div", null, h("h1", null, "Мониторинг сервера"),
          h("div", { className: "nirs-muted" }, (live ? "Prometheus · шаг 1 мин" : "Server Machine Dataset" + (state.machine ? " · " + state.machine : "")) +
            (latest ? " · Последний отсчёт " + (live ? new Date(latest.step * 1000).toLocaleString("ru-RU") : "#" + latest.step) : ""))),
        h("div", { className: "nirs-controls" },
          ...[["live", "Сервер"], ["smd", "Воспроизведение SMD"]].map(([key, label]) =>
            h("button", { key, type: "button", "aria-pressed": mode === key, onClick: () => { setMode(key); setInspection(null); } }, label)),
          h("select", { "aria-label": "Интервал графиков", value: (live ? minuteRanges : sampleRanges).includes(range) ? range : 240,
            onChange: (event) => { setRange(Number(event.target.value)); setInspection(null); } },
            ...(live ? minuteRanges : sampleRanges).map((value) => h("option", { key: value, value }, value + (live ? " мин" : " отсчётов")))))),
      error && h("p", { className: "nirs-alert", role: "alert" }, "Ошибка обновления · " + error),
      h("section", { className: "nirs-panel nirs-stack nirs-console", "aria-label": "Детектор аномалий" },
        h("div", { className: "nirs-line" },
          h("div", { className: "nirs-console-title" }, "Детектор аномалий и превентивная защита"),
          h("div", { className: "nirs-muted nirs-mono" }, "Isolation Forest + GRU · окно 12 отсчётов")),
        h("section", { className: "nirs-panel" },
          h("h2", null, "Классификация состояния"),
          h("div", { className: "nirs-banner", style: { color: stateColor, backgroundColor: stateColor + "12" } },
            h("strong", null, stateLabel), h("p", { className: "nirs-muted" }, statusText(state.status))),
          h("div", { className: "nirs-line" }, "Индекс аномальности",
            h("strong", { className: "nirs-number nirs-mono", style: { color: stateColor } }, format(point?.score, 3))),
          h("div", { className: "nirs-scale", "aria-hidden": "true" },
            Number.isFinite(point?.score) && h("div", { className: "nirs-scale-fill",
              style: { width: point.score * 100 + "%", backgroundColor: stateColor } }),
            ...thresholds.filter(([, value]) => Number.isFinite(value)).map(([label, value, color]) =>
              h("span", { key: label, title: label, className: "nirs-scale-marker", style: { left: value * 100 + "%", backgroundColor: color } }))),
          h("div", { className: "nirs-line nirs-muted nirs-mono" },
            "0,0", ...thresholds.map(([label, value, color]) =>
              h("span", { key: label, style: { color } }, label + " " + format(value, 3))), "1,0"),
          h("div", { className: "nirs-readouts nirs-mono" },
            ...[["Isolation Forest", format(point?.forest, 3)], ["GRU", format(point?.autoencoder, 3)],
              live ? ["Инференс", Number.isFinite(point?.inference_ms) ? format(point.inference_ms, 2) + " мс" : "—"] :
                ["Пакетный расчёт", state.benchmark ? format(state.benchmark.batch_ms_per_point, 3) + " мс/отсчёт" : "—"],
            ].map(([label, value]) => h("div", { key: label }, h("div", { className: "nirs-muted" }, label), h("strong", null, value))))),
        h("section", { className: "nirs-panel" },
          h("div", { className: "nirs-line" },
            h("h2", null, "Вклад метрик в ошибку реконструкции"),
            point?.top?.length > 0 && h("span", { className: "nirs-muted nirs-mono" }, "Топ-" + point.top.length + " из " + metricNames.length)),
          point?.top?.length ? point.top.map(({ metric, share }, index) =>
            h("div", { key: metric, style: { marginBottom: 11 } },
              h("div", { className: "nirs-line nirs-mono" }, names[metric]?.[0] || metric, format(share, 1) + "%"),
              h("progress", { value: share, max: 100, style: { accentColor: [colors.blue, colors.green, colors.yellow, colors.purple][index] },
                "aria-label": names[metric]?.[0] || metric }))) :
            h("p", { className: "nirs-muted" }, "Нет данных о вкладе для этого отсчёта"),
          h("div", { className: "nirs-line nirs-muted" }, "Среднеквадратичная ошибка (MSE)",
            h("strong", { className: "nirs-mono" }, format(point?.reconstruction_mse, 5)))),
        h("section", { className: "nirs-panel" },
          h("h2", null, "Контур превентивного управления"),
          h("div", { className: "nirs-readouts" }, h("div", null,
            h("div", { className: "nirs-muted" }, "Активный сценарий"),
            h("strong", null, snapshot?.action_mode === mode ?
              "Перезапуск демонстрационного HTTP-сервиса после трёх критических отсчётов" : "Пассивный мониторинг"))),
          h("p", { className: "nirs-muted" }, "Журнал реагирования"),
          h("ul", { className: "nirs-log nirs-mono" },
            ...(events.length ? events.map((event) => h("li", { key: event.time + "-" + event.step },
              new Date(event.time * 1000).toLocaleString("ru-RU"), " · ",
              h("span", { style: { color: event.status === "restarted" ? colors.green : colors.red } },
                event.status === "restarted" ? "PID " + event.old_pid + " → " + event.new_pid : event.error))) :
              [h("li", { key: "empty", className: "nirs-muted" }, "В этом режиме записей пока нет")])))),
      h("section", { className: "nirs-panel nirs-history" },
        h("h2", null, "Динамика индекса аномальности"),
        h(Graph, { points: recentScores, series: mainSeries.map(([key, name, color]) => ({ key, name, color, read: (item) => item[key] })),
          live, selected: point, thresholds, domain: [0, 1] }),
        h("div", { className: "nirs-legend" },
          ...mainSeries.map(([key, label, color]) => h("span", { key, style: { color } }, label)),
          ...thresholds.map(([label, , color]) => h("span", { key: label, style: { color } }, label)),
          !live && h("span", { style: { color: colors.red } }, "● Разметка SMD")),
        recentScores.length > 0 && h("p", { className: "nirs-muted" },
          "Критических отсчётов за интервал · " + criticalCount + " / " + recentScores.length),
        history.length > 1 && h("div", null,
          h("div", { className: "nirs-line" },
            h("label", { htmlFor: "nirs-sample" }, "Отсчёт · " + caption(selected.step, live)),
            h("button", { type: "button", onClick: () => setInspection(null), disabled: selected === latest }, "К последнему")),
          h("input", { id: "nirs-sample", type: "range", min: 0, max: history.length - 1,
            value: history.indexOf(selected), onChange: (event) => setInspection(history[Number(event.target.value)].step) }))),
      h("section", { className: "nirs-footer" },
        h("h2", null, live ? "Телеметрия VPS" : "Каналы SMD"),
        live ? h("div", { className: "nirs-stack" },
          ...liveGroups.map(([title, metrics]) => h(LiveGroup, { key: title, title, metrics, points: history, selected }))) :
          h("div", { className: "nirs-metrics" },
            ...metricNames.map((metric) => h(Metric, { key: metric, metric, points: history, selected, live })))),
      live && state.memory_guard && h(MemoryGuard, { guard: state.memory_guard }),
      live && state.memory_lab && h(MemoryGuard, { guard: state.memory_lab, lab: true }),
      state.benchmark && h(Benchmark, { state }));
  }

  return { plugin: new AppPlugin().setRootPage(Root) };
});

