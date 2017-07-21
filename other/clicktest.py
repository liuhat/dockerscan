#!/usr/bin/env python
# coding=utf-8
import click

@click.command()
@click.option('--count',default=1,help='Number of greetings.')
@click.option('--name',prompt='your name',help='the person to greet.')
def hello(count,name):
    """Simple program """
    for x in range(count):
        click.echo('hello %s!' % name)

if __name__=='__main__':
   hello()
