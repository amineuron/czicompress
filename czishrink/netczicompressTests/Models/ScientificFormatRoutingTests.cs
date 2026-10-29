// SPDX-FileCopyrightText: 2026 czicompress contributors
// SPDX-License-Identifier: GPL-3.0-or-later

namespace netczicompressTests.Models;

using System.IO.Abstractions.TestingHelpers;
using System.Reactive.Threading.Tasks;

/// <summary>
/// Checks selection of scientific files independently of the accompanying-file copy option.
/// </summary>
public class ScientificFormatRoutingTests
{
    [Theory]
    [InlineData(".czi", false, false, false, true)]
    [InlineData(".ND2", true, false, false, true)]
    [InlineData(".nd2", false, true, true, false)]
    [InlineData(".h5", false, true, false, true)]
    [InlineData(".HDF5", false, true, false, true)]
    [InlineData(".h5", true, false, true, false)]
    [InlineData(".tif", false, false, true, true)]
    [InlineData(".TIFF", false, false, true, true)]
    [InlineData(".tiff", true, true, false, false)]
    [InlineData(".csv", true, true, true, false)]
    public void EnabledFormatsDetermineProcessing(string extension, bool nikon, bool hdf5, bool tiff, bool expected)
    {
        var options = new ProcessingOptions(CompressionLevel.Default)
        {
            ProcessNikonNd2 = nikon,
            ProcessHdf5 = hdf5,
            ProcessTiff = tiff,
        };

        options.ProcessesExtension(extension).Should().Be(expected);
    }

    [Theory]
    [InlineData(true, false, false, "image.nd2")]
    [InlineData(false, true, false, "recording.h5")]
    [InlineData(false, false, true, "image.tiff")]
    public async Task EnabledFormatIsProcessedWithoutCopyOtherFiles(bool nikon, bool hdf5, bool tiff, string expected)
    {
        var fs = new MockFileSystem();
        string input = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString(), "input");
        string output = Path.Combine(Path.GetTempPath(), Guid.NewGuid().ToString(), "output");
        foreach (var name in new[] { "image.nd2", "recording.h5", "image.tiff", "notes.txt" })
        {
            fs.AddFile(Path.Combine(input, name), new MockFileData("data"));
        }

        List<string> processed = new();
        var processor = new Mock<IFileProcessor>();
        processor.Setup(p => p.ProcessFile(It.IsAny<string>(), It.IsAny<string>(), It.IsAny<ReportProgress>(), It.IsAny<CancellationToken>()))
            .Callback<string, string, ReportProgress, CancellationToken>((source, _, report, _) =>
            {
                processed.Add(Path.GetFileName(source));
                report(100);
            });
        var compressor = new MultiThreadedFolderCompressor((_, _) => processor.Object, new FileProcessingFailedHandler())
        {
            OtherFilesAttributesToSkip = FileAttributes.Hidden | FileAttributes.System,
        };
        var parameters = new FolderCompressorParameters(
            fs.DirectoryInfo.New(input),
            fs.DirectoryInfo.New(output),
            true,
            CompressionMode.CompressAll,
            new ExecutionOptions(new ThreadCount { Value = 1 }),
            new ProcessingOptions(CompressionLevel.Default)
            {
                ProcessNikonNd2 = nikon,
                ProcessHdf5 = hdf5,
                ProcessTiff = tiff,
            });
        var run = compressor.PrepareNewRun(parameters, CancellationToken.None);
        var completion = run.Output.ToTask();
        run.Start();
        await completion;

        processed.Should().Equal(expected);
    }
}
