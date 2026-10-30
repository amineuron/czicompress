// SPDX-FileCopyrightText: 2026 Amin Khosrozadeh
// SPDX-License-Identifier: GPL-3.0-or-later

namespace netczicompress.IntegrationChecks;

using System.IO.Abstractions;
using System.Reactive.Linq;
using System.Reactive.Threading.Tasks;
using System.Text.Json;

using netczicompress.Models;

/// <summary>
/// Exercises the same codecs, folder routing and cancellation as the desktop app.
/// </summary>
public static class Program
{
    public static async Task Main(string[] args)
    {
        if (args.Length != 2)
        {
            throw new ArgumentException("Supply a prepared fixture input folder and a new output folder.");
        }

        var fs = new FileSystem();
        var options = new ProcessingOptions(CompressionLevel.Default, CopyFailedFiles: false)
        {
            ProcessHdf5 = true,
            ProcessTiff = true,
        };
        var compressor = new MultiThreadedFolderCompressor(
            (mode, settings) => new MultiFormatFileProcessor(mode, settings),
            new FileProcessingFailedHandler());
        var parameters = new FolderCompressorParameters(
            fs.DirectoryInfo.New(args[0]),
            fs.DirectoryInfo.New(args[1]),
            true,
            CompressionMode.CompressAll,
            new ExecutionOptions(new ThreadCount { Value = 2 }),
            options);
        var run = compressor.PrepareNewRun(parameters, CancellationToken.None);
        List<CompressorMessage.FileFinished> results = new();
        using var subscription = run.Output.OfType<CompressorMessage.FileFinished>().Subscribe(results.Add);
        var completion = run.Output.ToTask();
        run.Start();
        await completion;
        Console.WriteLine(JsonSerializer.Serialize(results.Select(item => new
        {
            File = item.InputFile.Name,
            InputBytes = item.SizeInput,
            OutputBytes = item.SizeOutput,
            Error = item.ErrorMessage,
        })));
        if (results.Count != 3 || results.Any(item => item.ErrorMessage != null))
        {
            throw new IOException("Mixed-format integration failed.");
        }

        if (results.Any(item => item.Details?.InputSha256?.Length != 64 || item.Details.OutputSha256?.Length != 64))
        {
            throw new IOException("Per-file integrity evidence is missing.");
        }

        var decompressParameters = parameters with
        {
            InputDir = fs.DirectoryInfo.New(args[1]),
            OutputDir = fs.DirectoryInfo.New(args[1] + "-decompressed"),
            Mode = CompressionMode.Decompress,
        };
        var decompressRun = compressor.PrepareNewRun(decompressParameters, CancellationToken.None);
        List<CompressorMessage.FileFinished> decompressed = new();
        using var decompressSubscription = decompressRun.Output.OfType<CompressorMessage.FileFinished>().Subscribe(decompressed.Add);
        var decompression = decompressRun.Output.ToTask();
        decompressRun.Start();
        await decompression;
        if (decompressed.Count != 3 || decompressed.Any(item => item.ErrorMessage != null))
        {
            throw new IOException("Mixed-format decompression failed.");
        }

        static void WriteReport(string path, IEnumerable<CompressorMessage.FileFinished> messages)
        {
            var logger = new CsvLogFileWriter(File.CreateText(path));
            foreach (var message in messages)
            {
                logger.OnNext(message);
            }

            logger.OnCompleted();
        }

        WriteReport(Path.Combine(args[1], "compression-report.csv"), results);
        WriteReport(Path.Combine(decompressParameters.OutputDir.FullName, "decompression-report.csv"), decompressed);

        // Check cancellation through the actual worker and GUI temporary-file path.
        using var cancellation = new CancellationTokenSource();
        var cancelParameters = parameters with
        {
            InputDir = fs.DirectoryInfo.New(Path.Combine(args[0], "TIFF")),
            OutputDir = fs.DirectoryInfo.New(args[1] + "-canceled"),
        };
        var cancelRun = compressor.PrepareNewRun(cancelParameters, cancellation.Token);
        using var progress = cancelRun.Output.OfType<CompressorMessage.FileStarting>().Subscribe(
            start => start.ProgressPercent.Subscribe(percent =>
                {
                    if (percent > 0 && percent < 100)
                    {
                        cancellation.Cancel();
                    }
                }),
            _ => { });
        var canceledCompletion = cancelRun.Output.ToTask();
        cancelRun.Start();
        try
        {
            await canceledCompletion;
            throw new IOException("Cancellation was not delivered.");
        }
        catch (OperationCanceledException)
        {
            if (Directory.GetFiles(cancelParameters.OutputDir.FullName, "*", SearchOption.AllDirectories).Length != 0)
            {
                throw new IOException("Canceled operation left a partial output.");
            }
        }

        // Missing runtimes must produce a useful file error.
        using var missing = new ScientificFileProcessor(CompressionMode.CompressAll, CompressionLevel.Default, Path.Combine(args[1], "missing-tools"));
        try
        {
            missing.ProcessFile("absent.tif", "absent-output.tif", _ => { }, CancellationToken.None);
            throw new IOException("Missing runtime was not detected.");
        }
        catch (IOException error) when (error.Message.Contains("tools are missing", StringComparison.Ordinal))
        {
        }

        Console.WriteLine("Mixed-format compression, decompression, CSV evidence, cancellation, and missing-runtime checks passed.");
    }
}
