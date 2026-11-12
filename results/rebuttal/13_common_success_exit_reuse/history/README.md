# Complete screening histories

The two archives retain every candidate pool, screening decision, solver record,
and preliminary summary from the non-contextual and exit-context stages. The
final probe retains all stage-1 goals and only uses exit-context goals to fill
remaining slots. Preliminary summaries are not the final reported comparison.

To restore either archive from the experiment root:

```sh
tar -xzf history/non_contextual.tar.gz
tar -xzf history/exit_context.tar.gz
```

Each adjacent index lists original source paths, member names, sizes, and hashes.
