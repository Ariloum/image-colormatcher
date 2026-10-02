#target photoshop
/*
 * Калибровка фильтров Image ColorMatcher под Photoshop (T-0034).
 *
 * Запуск: Photoshop → File → Scripts → Browse… → этот файл.
 * Перед запуском: python-embed\python.exe tools\make_calibration.py
 * (создаёт calibration/hald_8.png и calibration/ps_jobs.jsx).
 *
 * Что делает: открывает hald_8.png отдельным документом; для каждого задания создаёт
 * корректирующий слой (Color Balance / Brightness/Contrast / Hue/Saturation) с нужными
 * значениями, сохраняет копию PNG в calibration/ps/, удаляет слой. В конце закрывает
 * документ БЕЗ сохранения. Другие открытые документы и настройки PS не трогает
 * (режим диалогов возвращается как был).
 *
 * Режимы:
 *   пробный — 5 контрольных файлов в calibration/ps/_trial/ (перезаписываются);
 *   полный  — приоритет 1 (41) или все (92) в calibration/ps/; уже готовые файлы
 *             пропускаются, поэтому прерванный прогон можно просто запустить снова.
 * Журнал — calibration/ps/_log.txt (или _trial/_log.txt).
 */

(function () {
    function cTID(s) { return charIDToTypeID(s); }
    function sTID(s) { return stringIDToTypeID(s); }

    // ---------- пути ----------
    var root = new File($.fileName).parent.parent;
    var calib = new Folder(root.fsName + "/calibration");
    var hald = new File(calib.fsName + "/hald_8.png");
    var jobsFile = new File(calib.fsName + "/ps_jobs.jsx");

    if (!hald.exists || !jobsFile.exists) {
        alert("Не найдены calibration/hald_8.png или calibration/ps_jobs.jsx.\n\n" +
              "Сначала выполните:\npython-embed\\python.exe tools\\make_calibration.py");
        return;
    }
    var allJobs = $.evalFile(jobsFile);

    // ---------- выбор режима ----------
    var trialMode = confirm("Калибровка ColorMatcher\n\n" +
        "ПРОБНЫЙ прогон — 5 контрольных файлов?\n\n" +
        "Да — пробный (рекомендуется первым)\nНет — перейти к полному прогону");
    var jobs = [];
    var i, n1 = 0;
    for (i = 0; i < allJobs.length; i++) { if (allJobs[i].prio === 1) { n1++; } }

    if (trialMode) {
        for (i = 0; i < allJobs.length; i++) { if (allJobs[i].trial) { jobs.push(allJobs[i]); } }
    } else {
        var takeAll = confirm("Полный прогон.\n\n" +
            "Да — ВСЕ файлы (" + allJobs.length + ")\nНет — только приоритет 1 (" + n1 + ")");
        for (i = 0; i < allJobs.length; i++) {
            if (takeAll || allJobs[i].prio === 1) { jobs.push(allJobs[i]); }
        }
    }

    var outDir = new Folder(calib.fsName + (trialMode ? "/ps/_trial" : "/ps"));
    if (!new Folder(calib.fsName + "/ps").exists) { new Folder(calib.fsName + "/ps").create(); }
    if (!outDir.exists) { outDir.create(); }

    // ---------- настройки слоёв ----------
    function intList(values) {
        var list = new ActionList();
        for (var k = 0; k < values.length; k++) { list.putInteger(values[k]); }
        return list;
    }

    function settingsFor(job) {
        var d = new ActionDescriptor();
        if (job.kind === "cb") {
            d.putList(cTID("ShdL"), intList(job.shadows));
            d.putList(cTID("MdtL"), intList(job.midtones));
            d.putList(cTID("HghL"), intList(job.highlights));
            d.putBoolean(cTID("PrsL"), job.pl);
            return { cls: "ClrB", desc: d };
        }
        if (job.kind === "bc") {
            d.putInteger(cTID("Brgh"), job.b);
            d.putInteger(cTID("Cntr"), job.c);
            d.putBoolean(sTID("useLegacy"), job.legacy);
            return { cls: "BrgC", desc: d };
        }
        if (job.kind === "hs") {
            d.putEnumerated(sTID("presetKind"), sTID("presetKindType"), sTID("presetKindCustom"));
            d.putBoolean(cTID("Clrz"), false);
            var master = new ActionDescriptor();
            master.putInteger(cTID("H   "), job.h);
            master.putInteger(cTID("Strt"), job.s);
            master.putInteger(cTID("Lght"), job.l);
            var adjs = new ActionList();
            adjs.putObject(cTID("Hst2"), master);
            d.putList(cTID("Adjs"), adjs);
            return { cls: "HStr", desc: d };
        }
        throw new Error("неизвестный вид задания: " + job.kind);
    }

    function makeAdjustmentLayer(job) {
        var s = settingsFor(job);

        // создать корректирующий слой с настройками
        var mk = new ActionDescriptor();
        var ref = new ActionReference();
        ref.putClass(cTID("AdjL"));
        mk.putReference(cTID("null"), ref);
        var layer = new ActionDescriptor();
        layer.putObject(cTID("Type"), cTID(s.cls), s.desc);
        mk.putObject(cTID("Usng"), cTID("AdjL"), layer);
        executeAction(cTID("Mk  "), mk, DialogModes.NO);

        // и задать их ещё раз через «set» — на случай, если Mk принял не всё
        var st = new ActionDescriptor();
        var tref = new ActionReference();
        tref.putEnumerated(cTID("AdjL"), cTID("Ordn"), cTID("Trgt"));
        st.putReference(cTID("null"), tref);
        st.putObject(cTID("T   "), cTID(s.cls), settingsFor(job).desc);
        try {
            executeAction(cTID("setd"), st, DialogModes.NO);
            return "";
        } catch (e) {
            return " (setd не прошёл: " + e + ")";  // настройки остались от Mk
        }
    }

    function removeExtraLayers(doc) {
        while (doc.layers.length > 1) { doc.layers[0].remove(); }
    }

    function savePngCopy(doc, file) {
        var opts = new PNGSaveOptions();
        opts.compression = 1;
        opts.interlaced = false;
        doc.saveAs(file, opts, true, Extension.LOWERCASE);
    }

    // ---------- прогон ----------
    var savedDialogs = app.displayDialogs;
    app.displayDialogs = DialogModes.NO;
    var log = [];
    var ok = 0, skipped = 0, failed = 0;
    var started = new Date();
    log.push("ColorMatcher calibration — " + started.toString());
    log.push("Photoshop " + app.version + ", режим: " + (trialMode ? "пробный" : "полный") +
             ", заданий: " + jobs.length);

    var doc = null;
    try {
        doc = app.open(hald);
        try { doc.bitsPerChannel = BitsPerChannelType.SIXTEEN; } catch (e1) { log.push("bitsPerChannel: " + e1); }
        log.push("документ: " + doc.name + ", бит: " + doc.bitsPerChannel +
                 ", профиль: " + (function () { try { return doc.colorProfileName; } catch (e) { return "нет"; } })());
        removeExtraLayers(doc);

        for (i = 0; i < jobs.length; i++) {
            var job = jobs[i];
            var target = new File(outDir.fsName + "/" + job.file);
            if (!trialMode && target.exists) { skipped++; continue; }
            try {
                var note = makeAdjustmentLayer(job);
                if (job.opacity !== 100) { doc.activeLayer.opacity = job.opacity; }
                savePngCopy(doc, target);
                ok++;
                log.push("ok    " + job.file + note);
            } catch (e2) {
                failed++;
                log.push("ОШИБКА " + job.file + ": " + e2 + (e2.line ? " (строка " + e2.line + ")" : ""));
            } finally {
                try { removeExtraLayers(doc); } catch (e3) { log.push("удаление слоя: " + e3); }
            }
        }
    } catch (fatal) {
        failed++;
        log.push("ФАТАЛЬНО: " + fatal + (fatal.line ? " (строка " + fatal.line + ")" : ""));
    } finally {
        if (doc) { try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e4) { /* уже закрыт */ } }
        app.displayDialogs = savedDialogs;
    }

    var secs = Math.round((new Date() - started) / 1000);
    log.push("готово: " + ok + ", пропущено (уже были): " + skipped + ", ошибок: " + failed + ", " + secs + " с");

    var logFile = new File(outDir.fsName + "/_log.txt");
    logFile.encoding = "UTF-8";
    if (logFile.open("w")) { logFile.write(log.join("\n") + "\n"); logFile.close(); }

    alert("Калибровка ColorMatcher\n\n" +
          "Готово: " + ok + "\nПропущено (уже были): " + skipped + "\nОшибок: " + failed +
          "\nВремя: " + secs + " с\n\nПапка: " + outDir.fsName +
          (failed ? "\n\nПодробности ошибок — в _log.txt" : ""));
})();
